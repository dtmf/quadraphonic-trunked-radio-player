#!/usr/bin/env python3

"""
simplestream-quad-audio-mixer.py

Receives multiple raw audio streams from trunk-recorder's simpleStream
plugin (using sendJSON) and mixes them into a single, continuous
4-channel (quadraphonic) raw audio stream written to stdout.

Each talkgroup is panned to a unique 2D (L/R, F/R) position.

The script also maintains a "active-talkgroups.txt" file for ffmpeg.

Designed to be piped directly to ffmpeg, e.g.:
python3 simplestream-quad-audio-mixer.py | ffmpeg ...
"""

import socket
import struct
import numpy as np
import threading
import time
import sys
import json
import hashlib
import atexit
import datetime
import os

# --- Configuration ---
UDP_IP = "0.0.0.0"          # IP to listen on (0.0.0.0 for all)
UDP_PORT = 7355             # Port to listen on (must match trunk-recorder)
CHUNK_MS = 40               # Audio chunk size in milliseconds (e.g., 40ms)
SAMPLE_RATE = 8000          # 8Khz, per trunk-recorder
DTYPE = np.int16            # s16le, per trunk-recorder
CHANNELS = 4                # 4-Channel (Quad) output
STREAM_TIMEOUT_S = 5.0      # How many seconds of silence to wait before culling a stream
STATUS_FILE = "active-talkgroups.txt" # File to write active call status for ffmpeg

# --- Calculated Constants ---
CHUNK_SAMPLES_MONO = int(SAMPLE_RATE * CHUNK_MS / 1000)
CHUNK_BYTES_MONO = CHUNK_SAMPLES_MONO * np.dtype(DTYPE).itemsize
CHUNK_SAMPLES_QUAD = CHUNK_SAMPLES_MONO * CHANNELS
CHUNK_BYTES_QUAD = CHUNK_SAMPLES_QUAD * np.dtype(DTYPE).itemsize

# 4-byte little-endian unsigned int for JSON length
JSON_LEN_HEADER = struct.Struct('<I')

# --- Shared State ---
# This dictionary holds the audio buffers and metadata for all active calls.
# It is protected by the stream_lock.
#
# active_streams = {
#     1001: {
#         "buffer": bytearray(),
#         "last_seen": time.time(),
#         "pan_l": 0.3,
#         "pan_r": 0.7,
#         "pan_f": 0.6,
#         "pan_r_rear": 0.4,
#         "tag": "FWPD 1",
#         "short_name": "FWPD Disp",
#         "src": "720001",
#         "audio_event_count": 0
#     }
# }
active_streams = {}
stream_lock = threading.Lock()
running = True

# --- Utility Functions ---

def log(message):
    """Logs a message to stderr with a timestamp."""
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    print(f"[{ts}] {message}", file=sys.stderr)

def get_pan_for_talkgroup(talkgroup_id):
    """
    Generates a stable, unique 2D pan position for a talkgroup.
    Returns (pan_l, pan_r, pan_f, pan_r_rear)
    """
    # Use a hash to get a stable "random" value for the talkgroup
    # We use a string to handle negative IDs
    h_lr = hashlib.sha256(f"lr-{talkgroup_id}".encode()).digest()
    h_fr = hashlib.sha256(f"fr-{talkgroup_id}".encode()).digest()
    
    # Convert first 2 bytes to a 0.0-1.0 value
    pan_lr_percent = (h_lr[0] * 256 + h_lr[1]) / 65535.0
    pan_fr_percent = (h_fr[0] * 256 + h_fr[1]) / 65535.0

    # Ensure pan is not 100% L/R or F/R (sounds bad)
    # Clamp to a range of 10% to 90%
    pan_lr_percent = np.clip(pan_lr_percent, 0.1, 0.9)
    pan_fr_percent = np.clip(pan_fr_percent, 0.1, 0.9)

    # Use "constant power" panning to avoid volume dips
    pan_l = np.cos(pan_lr_percent * np.pi / 2.0)
    pan_r = np.sin(pan_lr_percent * np.pi / 2.0)
    
    pan_f = np.cos(pan_fr_percent * np.pi / 2.0)
    pan_r_rear = np.sin(pan_fr_percent * np.pi / 2.0) # 'pan_r' is already used

    return (pan_l, pan_r, pan_f, pan_r_rear)

def update_status_file(streams, lock):
    """
    Writes the list of active talkgroups to the STATUS_FILE.
    This function is thread-safe.
    """
    lines_to_write = []
    try:
        with lock:
            # Create a list of human-readable strings
            if not streams:
                lines_to_write.append("Monitoring... (0 active calls)")
            else:
                lines_to_write.append(f"Active Calls: {len(streams)}")
                lines_to_write.append("-" * 20)
                # Sort by talkgroup ID for a stable display
                sorted_tgs = sorted(streams.keys())
                for tg_id in sorted_tgs:
                    stream = streams[tg_id]
                    # Format as: TG ID (short_name) - TAG
                    lines_to_write.append(f"TG {tg_id} ({stream['short_name']}) - {stream['tag']}")
        
        # Write to the file (outside the lock)
        # Use a temporary file and atomic rename to prevent ffmpeg
        # from reading a half-written file.
        temp_file = f"{STATUS_FILE}.tmp"
        with open(temp_file, "w") as f:
            f.write("\n".join(lines_to_write))
        
        # Atomic rename
        os.rename(temp_file, STATUS_FILE)

    except Exception as e:
        # Don't log if the lock is held, just fail silently
        if not lock.locked():
            log(f"ERROR: Could not write to status file {STATUS_FILE}: {e}")

# --- Threads ---

def udp_receive_thread():
    """
    Listens for UDP packets, parses them, and puts audio
    data into the correct buffer in active_streams.
    """
    global running
    log(f"Starting UDP receiver on {UDP_IP}:{UDP_PORT}...")
    
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536 * 16)
        udp_socket.bind((UDP_IP, UDP_PORT))
        udp_socket.settimeout(1.0) # 1-second timeout

        while running:
            try:
                data, addr = udp_socket.recvfrom(8192) # 8KB buffer
            except socket.timeout:
                continue # Just loop again
            
            if not running:
                break
                
            if not data:
                continue

            try:
                # --- JSON Header Parsing ---
                if len(data) < JSON_LEN_HEADER.size:
                    log(f"Packet too small for JSON header: {len(data)} bytes")
                    continue
                
                json_size = JSON_LEN_HEADER.unpack(data[:4])[0]
                json_end = 4 + json_size
                
                if json_end > len(data):
                    log(f"JSON size ({json_size}) exceeds packet length ({len(data)})")
                    continue
                    
                header_data = json.loads(data[4:json_end].decode('utf-8'))
                
                event = header_data.get("event", "audio")
                talkgroup_id = int(header_data.get("talkgroup", 0))
                tag = header_data.get("talkgroup_tag", "N/A")
                short_name = header_data.get("short_name", "N/A")
                src = header_data.get("src", "N/A")
                mono_bytes = data[json_end:]

                # --- Event Handling ---
                update_file = False
                
                if event == "call_start":
                    pan_l, pan_r, pan_f, pan_r_rear = get_pan_for_talkgroup(talkgroup_id)
                    with stream_lock:
                        if talkgroup_id not in active_streams:
                            active_streams[talkgroup_id] = {
                                "buffer": bytearray(),
                                "last_seen": time.time(),
                                "pan_l": pan_l,
                                "pan_r": pan_r,
                                "pan_f": pan_f,
                                "pan_r_rear": pan_r_rear,
                                "tag": tag,
                                "short_name": short_name,
                                "src": src,
                                "audio_event_count": 0
                            }
                            log(f"call_start: {short_name} TG {talkgroup_id} ({tag}) src: {src} at pan LR {pan_l:.2f}/FR {pan_f:.2f} (Active calls: {len(active_streams)})")
                            update_file = True
                        else:
                            # Call already active, just update metadata
                            active_streams[talkgroup_id]["tag"] = tag
                            active_streams[talkgroup_id]["short_name"] = short_name
                            active_streams[talkgroup_id]["src"] = src
                            active_streams[talkgroup_id]["last_seen"] = time.time()
                            log(f"Received call_start for active {short_name} TG {talkgroup_id} (src: {src})")
                            update_file = True # Update file on metadata change too

                elif event == "audio":
                    if not mono_bytes:
                        continue # Skip empty audio packets

                    # Filter out tiny keep-alive packets
                    if len(mono_bytes) < 100:
                        if talkgroup_id in active_streams:
                            # Just update the timestamp to prevent timeout
                            with stream_lock:
                                if talkgroup_id in active_streams:
                                     active_streams[talkgroup_id]["last_seen"] = time.time()
                        continue
                    
                    with stream_lock:
                        if talkgroup_id in active_streams:
                            active_streams[talkgroup_id]["buffer"].extend(mono_bytes)
                            active_streams[talkgroup_id]["last_seen"] = time.time()
                            active_streams[talkgroup_id]["audio_event_count"] += 1
                        else:
                            # Missed call_start! Create a default stream
                            pan_l, pan_r, pan_f, pan_r_rear = get_pan_for_talkgroup(talkgroup_id)
                            active_streams[talkgroup_id] = {
                                "buffer": bytearray(mono_bytes),
                                "last_seen": time.time(),
                                "pan_l": pan_l,
                                "pan_r": pan_r,
                                "pan_f": pan_f,
                                "pan_r_rear": pan_r_rear,
                                "tag": tag,
                                "short_name": short_name,
                                "src": src,
                                "audio_event_count": 1
                            }
                            log(f"Missed call_start! Creating stream for {short_name} TG {talkgroup_id} (Active calls: {len(active_streams)})")
                            update_file = True # <-- Set flag
                    
                    # --- Update Status File (Outside Lock) ---
                    # <-- FIX: Moved this block outside the 'with stream_lock'
                    if update_file:
                        update_status_file(active_streams, stream_lock)
                        
                elif event == "call_end":
                    with stream_lock:
                        if talkgroup_id in active_streams:
                            # Get metadata before deleting
                            stream_info = active_streams[talkgroup_id]
                            short_name = stream_info['short_name']
                            count = stream_info['audio_event_count']
                            
                            del active_streams[talkgroup_id]
                            log(f"call_end: {short_name} TG {talkgroup_id} after {count} audio events. (Active calls: {len(active_streams)})")
                            update_file = True
                        else:
                            pass # Ignore end event for a stream we don't have
                
                # --- Update Status File (Outside Lock) ---
                # <-- FIX: Moved this block outside the 'with stream_lock'
                if update_file and event != "audio": # 'audio' event handles its own update
                    update_status_file(active_streams, stream_lock)
            
            except json.JSONDecodeError as e:
                log(f"JSON decode error: {e}. Packet size: {len(data)}")
            except struct.error as e:
                log(f"Struct unpack error: {e}. Packet size: {len(data)}")
            except Exception as e:
                log(f"Unhandled packet error: {e}")

    except Exception as e:
        log(f"[UDP ERROR] An unexpected error occurred: {e}")
    finally:
        running = False
        log("UDP receiver thread stopped.")

def stdout_play_thread():
    """
    The "metronome" thread. Runs on a strict timer.
    Pulls audio from all active buffers, mixes them, and
    writes the final chunk to stdout.
    """
    global running
    log("Starting audio mixer. Writing to stdout...")
    log(f"Audio format: {SAMPLE_RATE} Hz, {np.dtype(DTYPE).name}, {CHANNELS}-Channel (Quad)")
    log(f"Chunk size: {CHUNK_MS}ms ({CHUNK_BYTES_QUAD} bytes)")
    
    # Create a reusable buffer for silence
    silent_chunk_quad = np.zeros(CHUNK_SAMPLES_QUAD, dtype=DTYPE)
    
    # Create a reusable float buffer for mixing
    mix_buffer_quad = np.zeros(CHUNK_SAMPLES_QUAD, dtype=np.float32)

    try:
        # Get stdout in binary mode
        out_pipe = sys.stdout.buffer
        
        start_time = time.time()
        next_chunk_time = start_time
        
        while running:
            # Clear the mix buffer
            mix_buffer_quad.fill(0)
            
            now = time.time()
            culled_streams = False
            
            with stream_lock:
                if not active_streams:
                    # No active streams, just send silence
                    out_pipe.write(silent_chunk_quad.tobytes())
                    out_pipe.flush() # <-- FIX: Flush stdout for silence
                else:
                    # --- Mix Active Streams ---
                    streams_to_cull = []
                    for tg_id, stream in active_streams.items():
                        
                        # Check for stream timeout
                        if (now - stream["last_seen"]) > STREAM_TIMEOUT_S:
                            streams_to_cull.append(tg_id)
                            continue
                            
                        # Check if we have enough data
                        if len(stream["buffer"]) >= CHUNK_BYTES_MONO:
                            # Pop one chunk of mono data
                            mono_bytes = stream["buffer"][:CHUNK_BYTES_MONO]
                            del stream["buffer"][:CHUNK_BYTES_MONO]
                            
                            # Convert to numpy array
                            mono_chunk = np.frombuffer(mono_bytes, dtype=DTYPE)
                            
                            # --- 4-Channel Panning ---
                            # This is the core 2D panning logic.
                            # We create 4 channels from the 1 mono channel.
                            
                            # 1. Convert to float for mixing
                            mono_float = mono_chunk.astype(np.float32)
                            
                            # 2. Apply F/R pan
                            f_channel = mono_float * stream["pan_f"]
                            r_channel = mono_float * stream["pan_r_rear"]
                            
                            # 3. Apply L/R pan to F/R channels
                            # This creates 4 distinct channels: FL, FR, RL, RR
                            # c0 = FL, c1 = FR, c2 = RL, c3 = RR
                            
                            # "De-interleave" the mix buffer for easier mixing
                            # mix_buffer_quad[0::4] -> all Front-Left samples
                            # mix_buffer_quad[1::4] -> all Front-Right samples
                            # mix_buffer_quad[2::4] -> all Rear-Left samples
                            # mix_buffer_quad[3::4] -> all Rear-Right samples
                            
                            mix_buffer_quad[0::4] += f_channel * stream["pan_l"] # FL
                            mix_buffer_quad[1::4] += f_channel * stream["pan_r"] # FR
                            mix_buffer_quad[2::4] += r_channel * stream["pan_l"] # RL
                            mix_buffer_quad[3::4] += r_channel * stream["pan_r_rear"] # RR
                    
                    # --- Finalize Mix ---
                    # Clip the mixed audio to prevent overflow
                    np.clip(mix_buffer_quad, -32768, 32767, out=mix_buffer_quad)
                    
                    # Convert back to int16
                    final_chunk_quad = mix_buffer_quad.astype(DTYPE)
                    
                    # Write to stdout
                    out_pipe.write(final_chunk_quad.tobytes())
                    out_pipe.flush() # <-- FIX: Flush stdout for mixed audio

                    # --- Cull Timed-out Streams ---
                    if streams_to_cull:
                        culled_streams = True
                        for tg_id in streams_to_cull:
                            log(f"{active_streams[tg_id]['short_name']} TG {tg_id} (timeout - Missed call_end?) (Active calls: {len(active_streams) - 1})")
                            del active_streams[tg_id]
            
            # If we culled, update the status file
            if culled_streams:
                update_status_file(active_streams, stream_lock)
            
            # --- Metronome Logic ---
            # Calculate the time for the next chunk
            next_chunk_time += (CHUNK_MS / 1000.0)
            
            # Sleep until the next chunk time
            sleep_time = next_chunk_time - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif sleep_time < -0.1:
                # We're more than 100ms behind!
                # This is bad, but just reset the timer to now.
                log(f"WARN: Audio mixer is lagging! ({sleep_time:.2f}s)")
                next_chunk_time = time.time()

    except BrokenPipeError:
        log("WARN: Broken pipe. (ffmpeg probably closed)")
    except Exception as e:
        log(f"[STDOUT ERROR] An unexpected error occurred: {e}")
    finally:
        running = False
        log("Audio mixer thread stopped.")
        # Clear the status file on exit
        try:
            with open(STATUS_FILE, "w") as f:
                f.write("Offline")
        except:
            pass # Don't worry if it fails

# --- Main ---
def main():
    global running
    
    # Create and start threads
    t_udp = threading.Thread(target=udp_receive_thread, daemon=True)
    t_play = threading.Thread(target=stdout_play_thread, daemon=True)
    
    t_udp.start()
    t_play.start()
    
    def on_exit():
        global running
        running = False
        log("Shutdown signal received...")
        
    atexit.register(on_exit)
    
    try:
        while running:
            # Keep main thread alive to catch signals
            time.sleep(0.5)
            # Check if threads are alive
            if not t_udp.is_alive():
                log("ERROR: UDP receiver thread died. Exiting.")
                running = False
            if not t_play.is_alive():
                log("ERROR: Audio mixer thread died. Exiting.")
                running = False
                
    except KeyboardInterrupt:
        pass
    
    running = False
    log("Shutting down...")
    t_udp.join(timeout=1.0)
    t_play.join(timeout=1.0)
    log("Shutdown complete.")

if __name__ == "__main__":
    main()
