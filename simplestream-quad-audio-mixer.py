#!/usr/bin/env python3
"""
Receives trunk-recorder audio streams via UDP (with JSON headers),
mixes them in real-time with quadraphonic panning based on talkgroup,
and outputs a continuous raw s16le 4-CHANNEL stream to stdout
for piping to ffmpeg.

Receives audio from trunk-recorder's "simpleStream" plugin.

"""

import socket
import sys
import time
import threading
import struct
import numpy as np
import hashlib
import json
import datetime

# --- Configuration ---
UDP_IP = "0.0.0.0"
UDP_PORT = 7355

# Audio format from trunk-recorder
SAMPLE_RATE = 8000
AUDIO_FORMAT = np.int16
AUDIO_CHANNELS_IN = 1

# Audio format for output (piping to ffmpeg)
AUDIO_CHANNELS_OUT = 4  # CHANGED: 2 -> 4 (Quadraphonic: FL, FR, RL, RR)
AUDIO_WIDTH_BYTES = np.dtype(AUDIO_FORMAT).itemsize  # 2 bytes for s16le

# Processing chunk size
CHUNK_MS = 40  # 40ms audio chunks (good for low latency)

# Calculated constants
CHUNK_SAMPLES_PER_CHANNEL = int(SAMPLE_RATE * (CHUNK_MS / 1000.0))
CHUNK_BYTES_MONO = CHUNK_SAMPLES_PER_CHANNEL * AUDIO_WIDTH_BYTES * AUDIO_CHANNELS_IN
# RENAMED: CHUNK_BYTES_STEREO -> CHUNK_BYTES_OUT
CHUNK_BYTES_OUT = CHUNK_SAMPLES_PER_CHANNEL * AUDIO_WIDTH_BYTES * AUDIO_CHANNELS_OUT
# RENAMED: SILENT_CHUNK_STEREO -> SILENT_CHUNK_OUT
SILENT_CHUNK_OUT = np.zeros(CHUNK_SAMPLES_PER_CHANNEL * AUDIO_CHANNELS_OUT, dtype=AUDIO_FORMAT)

# Stream management
STREAM_TIMEOUT_S = 5.0  # Cull stream after 5 seconds of no audio

# Protocol headers
JSON_LEN_HEADER = struct.Struct('<I')  # 4-byte unsigned int (little-endian)

# --- Shared State ---
# This dictionary is the central, thread-safe state.
#
# active_streams = {
#     <talkgroup_id_int>: {
#         "buffer": bytearray(),
#         "last_seen": time.time(),
#         "pan_lr": 0.75,               # 0.0 (L) to 1.0 (R)
#         "pan_fr": 0.25,               # 0.0 (F) to 1.0 (R)
#         ...
#     }
# }
active_streams = {}
stream_lock = threading.Lock()
running = True

def log(message):
    """Prints a message to stderr with a millisecond timestamp."""
    now = datetime.datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}.{now.microsecond//1000:03d}] {message}", file=sys.stderr)

def get_pan_for_talkgroup(talkgroup_id):
    """
    Creates stable, unique 2D pan positions for a talkgroup.
    Returns (pan_lr, pan_fr)
    - pan_lr: 0.0 (100% Left) to 1.0 (100% Right)
    - pan_fr: 0.0 (100% Front) to 1.0 (100% Rear)
    """
    hash_obj = hashlib.sha1(str(talkgroup_id).encode('utf-8'))
    hash_bytes = hash_obj.digest()
    
    # Use first 2 bytes for Left/Right pan
    hash_val_lr = int.from_bytes(hash_bytes[0:2], 'little')
    pan_lr = hash_val_lr / 65535.0
    
    # Use next 2 bytes for Front/Rear pan
    hash_val_fr = int.from_bytes(hash_bytes[2:4], 'little')
    pan_fr = hash_val_fr / 65535.0
    
    return pan_lr, pan_fr

def udp_receive_thread():
    """
    Listens for UDP packets, parses JSON, and buffers audio.
    This thread is I/O bound.
    """
    global running
    log(f"Starting UDP receiver on {UDP_IP}:{UDP_PORT}...")
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind((UDP_IP, UDP_PORT))
        udp_socket.settimeout(1.0)  # Check for 'running' flag every 1s
    except OSError as e:
        log(f"ERROR: Could not bind to port {UDP_PORT}: {e}")
        log(f"ERROR: Is another instance running?")
        running = False
        return

    while running:
        try:
            data, addr = udp_socket.recvfrom(8192)

            if not data or len(data) <= JSON_LEN_HEADER.size:
                continue
            
            short_name = "N/A"
            
            try:
                json_size = JSON_LEN_HEADER.unpack(data[:JSON_LEN_HEADER.size])[0]
                json_start = JSON_LEN_HEADER.size
                json_end = json_start + json_size
                json_header = json.loads(data[json_start:json_end].decode('utf-8'))
            except Exception as e:
                log(f"WARN: Could not parse JSON header. Error: {e}")
                continue
                
            event = json_header.get("event")
            src_id = json_header.get("src", -1) 
            now = time.time()

            if event == "call_start":
                talkgroup_id = int(json_header.get("talkgroup", 0))
                if talkgroup_id == 0:
                    continue
                
                tag = json_header.get("talkgroup_tag", "Unknown")
                short_name = json_header.get("short_name", "N/A")

                with stream_lock:
                    if talkgroup_id in active_streams:
                        active_streams[talkgroup_id]["tag"] = tag
                        active_streams[talkgroup_id]["short_name"] = short_name
                        active_streams[talkgroup_id]["last_seen"] = now
                        active_streams[talkgroup_id]["audio_event_count"] = 0 
                        active_streams[talkgroup_id]["src_id"] = src_id 
                        log(f"Received call_start for active {short_name} TG {talkgroup_id} (src: {src_id})")
                    else:
                        pan_lr, pan_fr = get_pan_for_talkgroup(talkgroup_id)
                        active_streams[talkgroup_id] = {
                            "buffer": bytearray(),
                            "last_seen": now,
                            "pan_lr": pan_lr,
                            "pan_fr": pan_fr,
                            "tg_id": talkgroup_id,
                            "tag": tag,
                            "short_name": short_name,
                            "audio_event_count": 0,
                            "src_id": src_id
                        }
                        log(f"call_start: {short_name} TG {talkgroup_id} ({tag}) (src: {src_id}) at pan LR:{pan_lr:.2f}/FR:{pan_fr:.2f} (Active calls: {len(active_streams)})")

            elif event == "audio":
                talkgroup_id = int(json_header.get("talkgroup", 0))
                if talkgroup_id == 0:
                    continue

                mono_bytes = data[json_end:]
                if not mono_bytes or len(mono_bytes) < 100:
                    continue

                with stream_lock:
                    if talkgroup_id not in active_streams:
                        pan_lr, pan_fr = get_pan_for_talkgroup(talkgroup_id)
                        short_name = json_header.get("short_name", "N/A")
                        active_streams[talkgroup_id] = {
                            "buffer": bytearray(mono_bytes),
                            "last_seen": now,
                            "pan_lr": pan_lr,
                            "pan_fr": pan_fr,
                            "tg_id": talkgroup_id,
                            "tag": "Unknown",
                            "short_name": short_name, 
                            "audio_event_count": 1, 
                            "src_id": src_id
                        }
                        log(f"WARN: Missed call_start! Creating stream for {short_name} TG {talkgroup_id} (Active calls: {len(active_streams)})")
                    else:
                        active_streams[talkgroup_id]["buffer"].extend(mono_bytes)
                        active_streams[talkgroup_id]["last_seen"] = now
                        active_streams[talkgroup_id]["audio_event_count"] += 1

            elif event == "call_end":
                talkgroup_id = int(json_header.get("talkgroup", 0))
                if talkgroup_id == 0:
                    continue
                
                with stream_lock:
                    if talkgroup_id in active_streams:
                        event_count = active_streams[talkgroup_id]["audio_event_count"]
                        short_name = active_streams[talkgroup_id]["short_name"]
                        log(f"call_end: {short_name} TG {talkgroup_id} after {event_count} audio events. (Active calls: {len(active_streams) - 1})")
                        del active_streams[talkgroup_id]
                    else:
                        pass 
            
            else:
                pass

        except socket.timeout:
            continue
        except UnboundLocalError as e:
            log(f"WARN: UnboundLocalError (harmless on startup): {e}")
            continue
        except Exception as e:
            if running:
                log(f"ERROR: An unexpected error occurred: {e}")

    log("UDP receiver thread stopped.")
    udp_socket.close()

def stdout_play_thread():
    """
    Runs a real-time loop, pulling, mixing, and writing audio to stdout.
    This thread is CPU/time bound.
    """
    global running
    log(f"Starting audio mixer. Writing to stdout...")
    log(f"Audio format: {SAMPLE_RATE} Hz, s16le, 4-Channel Quadraphonic")
    log(f"Chunk size: {CHUNK_MS}ms ({CHUNK_BYTES_OUT} bytes)")
    
    try:
        stdout_buffer = sys.stdout.buffer
    except Exception as e:
        log(f"ERROR: Could not open stdout.buffer: {e}")
        log(f"ERROR: Are you piping this script to another command?")
        running = False
        return

    from time import sleep, perf_counter
    interval = CHUNK_MS / 1000.0
    
    # We will mix audio into this float buffer
    # It's persistent to avoid re-allocating memory in the loop
    mix_buffer_float = np.zeros(CHUNK_SAMPLES_PER_CHANNEL * AUDIO_CHANNELS_OUT, dtype=np.float32)

    next_frame_time = perf_counter()

    while running:
        try:
            while perf_counter() < next_frame_time:
                sleep(0.0001)
            next_frame_time += interval
            
            now = time.time()
            streams_to_mix = []
            streams_to_cull = []

            mix_buffer_float.fill(0) # Reset the mix buffer
            has_audio = False

            with stream_lock:
                for tg_id, stream in active_streams.items():
                    if (now - stream["last_seen"]) > STREAM_TIMEOUT_S:
                        streams_to_cull.append(tg_id)
                        continue
                    
                    if len(stream["buffer"]) >= CHUNK_BYTES_MONO:
                        streams_to_mix.append(stream)
                
                for stream in streams_to_mix:
                    mono_bytes = stream["buffer"][:CHUNK_BYTES_MONO]
                    del stream["buffer"][:CHUNK_BYTES_MONO]

                    mono_float = np.frombuffer(mono_bytes, dtype=AUDIO_FORMAT).astype(np.float32)

                    # --- 2D QUADRAPHONIC PANNING ---
                    L = (1.0 - stream["pan_lr"])
                    R = stream["pan_lr"]
                    F = (1.0 - stream["pan_fr"])
                    B = stream["pan_fr"] # B for Back/Rear

                    # Apply 4-corner amplitude panning and add to the mix buffer
                    # Channel 0: Front Left
                    mix_buffer_float[0::4] += mono_float * F * L
                    # Channel 1: Front Right
                    mix_buffer_float[1::4] += mono_float * F * R
                    # Channel 2: Rear Left
                    mix_buffer_float[2::4] += mono_float * B * L
                    # Channel 3: Rear Right
                    mix_buffer_float[3::4] += mono_float * B * R
                    
                    has_audio = True

                for tg_id in streams_to_cull:
                    if tg_id in active_streams:
                        event_count = active_streams[tg_id]["audio_event_count"]
                        short_name = active_streams[tg_id]["short_name"]
                        log(f"timeout: {short_name} TG {tg_id} after {event_count} audio events (Missed call_end?) (Active calls: {len(active_streams) - 1})")
                        del active_streams[tg_id]

            if has_audio:
                np.clip(mix_buffer_float, -32768, 32767, out=mix_buffer_float)
                final_chunk = mix_buffer_float.astype(AUDIO_FORMAT)
                stdout_buffer.write(final_chunk.tobytes())
            else:
                stdout_buffer.write(SILENT_CHUNK_OUT.tobytes())
            
            stdout_buffer.flush()

        except BrokenPipeError:
            log("WARN: Broken pipe. (ffmpeg probably closed)")
            running = False
        except Exception as e:
            if running:
                log(f"ERROR: An unexpected error occurred: {e}")
                running = False

    log("Audio mixer thread stopped.")

# --- Main Execution ---
if __name__ == "__main__":
    if not sys.stdout.isatty():
        t_udp = threading.Thread(target=udp_receive_thread, daemon=True)
        t_play = threading.Thread(target=stdout_play_thread, daemon=True)
        t_udp.start()
        t_play.start()
        try:
            while running:
                time.sleep(1.0)
                if not t_udp.is_alive() or not t_play.is_alive():
                    running = False
        except KeyboardInterrupt:
            log("\nCaught Ctrl+C, shutting down...")
            running = False
        t_udp.join(timeout=2.0)
        t_play.join(timeout=2.0)
        log("Shutdown complete.")
    else:
        print("This script is designed to be piped to ffmpeg.", file=sys.stderr)
        print("It cannot write audio data to the terminal.", file=sys.stderr)
        sys.exit(1)
