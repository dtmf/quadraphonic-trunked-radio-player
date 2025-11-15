# **Quadraphonic Trunked Radio Streamer/Player**

## **Project Summary**

This project consists of a Python script (simplestream-quad-audio-mixer.py) that acts as a real-time, multi-call audio mixer for [trunk-recorder](https://github.com/robotastic/trunk-recorder).  

It listens on a UDP port for trunk-recorder's simpleStream JSON events. As multiple, concurrent calls are received, it mixes them into a single, continuous 4-channel (quadraphonic) raw audio stream.  

Each unique talkgroup is assigned a stable 2D "pan" (Left/Right and Front/Rear) based on a hash of its ID. This creates an immersive, "in-the-center-of-the-room" listening experience where you can spatially distinguish different conversations.  

The script's 4-channel raw audio output is sent to stdout, designed to be piped directly into ffmpeg for encoding and streaming.

The script also generates a text file (active-talkgroups.txt) that can be used by ffmpeg to overlay a live list of currently active talkgroups onto a video stream.

## **Example output**

[sample-av-output.mp4](https://github.com/dtmf/quadraphonic-trunked-radio-player/raw/refs/heads/main/sample-av-output.mp4)

## **Requirements**

* Python 3  
* Numpy (pip install numpy)  
* [trunk-recorder](https://github.com/robotastic/trunk-recorder) (configured for simpleStream with JSON)  
* [ffmpeg](https://ffmpeg.org/) (for encoding and streaming the script's output)

## **trunk-recorder Configuration**

To use this script, you must enable audioStreaming, and enable the simpleStream plugin in your trunk-recorder config.json. This example enables JSON-based streaming of all channels of all systems to the script's UDP port:

    "audioStreaming": true,

    "plugins": [{
        "name":"simpletream",
        "library":"libsimplestream.so",
        "streams":[{
            "TGID":0,
            "useTCP":false,
            "address":"127.0.0.1",
            "port":7355,
            "sendCallStart":true,
            "sendCallEnd":true,
            "sendJSON":true
        }]
    }],

**Note:** Adjust the address and port to match your setup. The script must be listening on the same port.

## **Usage**

### **Example 1: Live 5.1 Surround Sound + Video, streamed to kodi**

This example takes the 4-channel audio, maps it into a 5.1 Dolby Digital (AC-3) stream, and combines it with a 5fps video track showing the live contents of active-talkgroups.txt. It then broadcasts this combined audio/video stream over your local network using UDP.  

Run this command on your trunked-radio server, or another server.

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad \
        -thread_queue_size 1024 -i pipe:0 \
        -f lavfi -i "color=c=black:s=1280x720:r=5" \
        -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=24:fontcolor=white:x=10:y=10:textfile=active-talkgroups.txt:reload=1" \
        -af "pan=5.1|c0=c0|c1=c1|c4=c2|c5=c3,aresample=48000" \
        -c:a ac3 -b:a 448k \
        -c:v libx264 -preset ultrafast -tune zerolatency -g 10 \
        -f mpegts \
        "udp://YOUR_KODI_IP:1234?pkt_size=1316"

You may need to change the fontfile= path to a valid font on your system

YOUR\_KODI\_IP: Change this to the IP address of your Kodi device

On Kodi: Create a text file named Quad\_Radio.strm with the following line and play it:

    udp://@:1234

### **Example 2: Live 5.1 Surround Sound + Video (volume increased, LFE channel added), saved to a file**

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad \
        -thread_queue_size 1024 -i pipe:0 \
        -f lavfi -i "color=c=black:s=1280x720:r=5" \
        -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=24:fontcolor=white:x=10:y=10:textfile=active-talkgroups.txt:reload=1" \
	-af "pan=5.1|c0=3*c0|c1=3*c1|c3=0.9*c0+0.9*c1+0.9*c2+0.9*c3|c4=3*c2|c5=3*c3,aresample=48000" \
        -c:a ac3 -b:a 448k \
        -c:v libx264 -preset ultrafast -tune zerolatency -g 10 \
        -t 60 \
        sample-av-output.mp4

### **Example 3: Local Stereo Playback (Audio-Only)**

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad -i pipe:0 \
        -af "pan=stereo|c0=c0+c2|c1=c1+c3" \
        -f pulse "Trunked Radio Mix"

You should see "Trunked Radio Mix" appear as a playback stream in your desktop's volume mixer (e.g., pavucontrol).

## **Example script output:**

```
[2025-11-14 13:36:00.749] Received call_start for active TG 5860 [L2FW] (src: 7205275)
[2025-11-14 13:36:01.134] Received call_start for active TG 6584 [L2FW] (src: 7219791)
[2025-11-14 13:36:01.650] call_start: TG 5398 [L2FW] FW FD Locut (src: 7200021) at pan LR 0.88/FR 0.93 (Active calls: 13)
[2025-11-14 13:36:02.010] call_end: TG 7767 [L1FW] after 20 audio events. (Active calls: 12)
[2025-11-14 13:36:02.843] TG 5047 [L1FW] (timeout - Missed call_end?) (Active calls: 11)
[2025-11-14 13:36:02.883] TG 5007 [L1FW] (timeout - Missed call_end?) (Active calls: 10)
[2025-11-14 13:36:02.883] TG 5289 [L1FW] (timeout - Missed call_end?) (Active calls: 9)
[2025-11-14 13:36:02.926] Received call_start for active TG 6230 [L2FW] (src: 7216602)
[2025-11-14 13:36:03.049] call_start: TG 6414 [L1FW] NET PD Pat 1 (src: 7218815) at pan LR 0.40/FR 0.16 (Active calls: 10)
[2025-11-14 13:36:03.627] call_start: TG 6232 [L2FW] NRHW PD Pat2 (src: 7216930) at pan LR 0.79/FR 0.75 (Active calls: 11)
[2025-11-14 13:36:04.443] TG 5121 [L1FW] (timeout - Missed call_end?) (Active calls: 10)
[2025-11-14 13:36:04.589] call_start: TG 5121 [L1FW] FWPD E-TLK1 (src: 7204809) at pan LR 0.26/FR 0.82 (Active calls: 11)
[2025-11-14 13:36:04.590] Received call_start for active TG 5860 [L2FW] (src: 7205269)
[2025-11-14 13:36:06.029] call_end: TG 6588 [L2FW] after 66 audio events. (Active calls: 10)
[2025-11-14 13:36:06.123] Received call_start for active TG 6824 [L2FW] (src: 7228352)
[2025-11-14 13:36:06.832] call_start: TG 5588 [L2FW] FW Wtr Dsp 5 (src: 7215657) at pan LR 0.40/FR 0.81 (Active calls: 11)
[2025-11-14 13:36:06.833] Received call_start for active TG 6232 [L2FW] (src: 7201065)
[2025-11-14 13:36:07.009] call_end: TG 6584 [L2FW] after 7 audio events. (Active calls: 10)
[2025-11-14 13:36:07.085] call_start: TG 5007 [L1FW] FWPD-CENTRAL (src: 7215959) at pan LR 0.96/FR 0.98 (Active calls: 11)
[2025-11-14 13:36:07.086] call_start: TG 5289 [L1FW] FW AIR-One (src: 7215959) at pan LR 0.29/FR 0.88 (Active calls: 12)
[2025-11-14 13:36:07.149] call_start: TG 5047 [L1FW] FWPD W-PTRLl (src: 7215959) at pan LR 0.52/FR 0.72 (Active calls: 13)
[2025-11-14 13:36:07.402] call_start: TG 7767 [L1FW] - (src: 7228012) at pan LR 0.80/FR 0.40 (Active calls: 14)
[2025-11-14 13:36:07.534] Received call_start for active TG 6414 [L1FW] (src: 7207356)
[2025-11-14 13:36:07.595] Received call_start for active TG 5860 [L2FW] (src: 7205275)
[2025-11-14 13:36:07.849] Received call_start for active TG 5007 [L1FW] (src: 7200010)
[2025-11-14 13:36:07.850] Received call_start for active TG 5047 [L1FW] (src: 7200010)
[2025-11-14 13:36:07.851] Received call_start for active TG 5289 [L1FW] (src: 7200010)
[2025-11-14 13:36:08.621] call_start: TG 6588 [L2FW] TM-MITS Op (src: 7219743) at pan LR 0.70/FR 0.99 (Active calls: 15)
[2025-11-14 13:36:09.021] call_end: TG 6230 [L2FW] after 43 audio events. (Active calls: 14)
[2025-11-14 13:36:09.130] Received call_start for active TG 5515 [L1FW] (src: 7201664)
[2025-11-14 13:36:09.131] call_start: TG 5799 [L1FW] WestoverHill PD1 (src: 7203837) at pan LR 0.82/FR 0.90 (Active calls: 15)
[2025-11-14 13:36:09.602] TG 5121 [L1FW] (timeout - Missed call_end?) (Active calls: 14)
[2025-11-14 13:36:10.010] call_end: TG 5515 [L1FW] after 54 audio events. (Active calls: 13)
[2025-11-14 13:36:10.052] Missed call_start! Creating stream for TG 5515 [L1FW] (Active calls: 14)
```
## **Notes**

This project is unlikely to be useful to anyone. It was fun to vibe code.
