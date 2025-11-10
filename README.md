# **Quadraphonic Trunked Radio Streamer/Player**

## **Project Summary**

This project consists of a Python script (simplestream-quad-audio-mixer.py) that acts as a real-time, multi-call audio mixer for [trunk-recorder](https://github.com/robotastic/trunk-recorder).  

It listens on a UDP port for trunk-recorder's simpleStream JSON events. As multiple, concurrent calls are received, it mixes them into a single, continuous 4-channel (quadraphonic) raw audio stream.  

Each unique talkgroup is assigned a stable 2D "pan" (Left/Right and Front/Rear) based on a hash of its ID. This creates an immersive, "in-the-center-of-the-room" listening experience where you can spatially distinguish different conversations.  

The script's 4-channel raw audio output is sent to stdout, designed to be piped directly into ffmpeg for encoding and streaming.

The script also generates a text file (active-talkgroups.txt) that can be used by ffmpeg to overlay a live list of currently active talkgroups onto a video stream.

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

### **Example 1: Live 5.1 Surround Sound (with Video Overlay) streamed to kodi**

This example takes the 4-channel audio, maps it into a 5.1 Dolby Digital (AC-3) stream, and combines it with a 5fps video track showing the live contents of active-talkgroups.txt. It then broadcasts this combined audio/video stream over your local network using UDP.  

(Run this command on your trunked-radio server, or another server.)

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad \
        -thread_queue_size 1024 -i pipe:0 \
        -f lavfi -i "color=c=black:s=1280x720:r=5" \
        -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=24:fontcolor=white:x=10:y=10:textfile=active-talkgroups.txt:reload=1" \
        -af "pan=5.1|c0=c0|c1=c1|c4=c2|c5=c3" \
        -c:a ac3 -b:a 448k \
        -c:v libx264 -preset ultrafast -tune zerolatency \
        -f mpegts \
        "udp://YOUR_KODI_IP:1234?pkt_size=1316"

You may need to change the fontfile= path to a valid font on your system

YOUR\_KODI\_IP**: Change this to the IP address of your Kodi device

On Kodi: Create a text file named Quad\_Radio.strm with the following line and play it:

    udp://@:1234

### **Example 2: Live 5.1 Surround Sound (with Video Overlay) saved to a file**

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad \
        -thread_queue_size 1024 -i pipe:0 \
        -f lavfi -i "color=c=black:s=1280x720:r=5" \
        -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=24:fontcolor=white:x=10:y=10:textfile=active-talkgroups.txt:reload=1" \
        -af "pan=5.1|c0=c0|c1=c1|c4=c2|c5=c3" \
        -c:a ac3 -b:a 448k \
        -c:v libx264 -preset ultrafast -tune zerolatency \
        -t 60 \
        quad-tr.mkv

### **Example 3: Local Stereo Playback (Audio-Only)**

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad -i pipe:0 \
        -af "pan=stereo|c0=c0+c2|c1=c1+c3" \
        -f pulse "Trunked Radio Mix"

You should see "Trunked Radio Mix" appear as a playback stream in your desktop's volume mixer (e.g., pavucontrol).

## **Example script output:**
coming soon

## **Example audio output:**
coming soon

## **AI-Assisted Development**

This project was developed using a "vibe coding" approach, in close collaboration with Google's Gemini. I provided the high-level architecture, core logic, and iterative debugging, while Gemini assisted in generating boilerplate code, refining ffmpeg commands, and exploring different streaming protocols. As the human author, I have reviewed, tested, and take full responsibility for the final code.

