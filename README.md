# **Quadraphonic Trunked Radio Streamer/Player**

## **Project Summary**

This project consists of a Python script (simplestream-quad-audio-mixer.py) that acts as a real-time, multi-call audio mixer for [trunk-recorder](https://github.com/robotastic/trunk-recorder).  
It listens on a UDP port for trunk-recorder's simpleStream JSON events. As multiple, concurrent calls (call\_start, audio, call\_end) are received, it mixes them into a single, continuous 4-channel (quadraphonic) raw audio stream.  

Each unique talkgroup is assigned a stable 2D "pan" (Left/Right and Front/Rear) based on a hash of its ID. This creates an immersive, "in-the-center-of-the-room" listening experience where you can spatially distinguish different conversations.  

The script's 4-channel raw audio output is sent to stdout, designed to be piped directly into ffmpeg for encoding and streaming.

## **Requirements**

* Python 3  
* Numpy (pip install numpy)  
* [trunk-recorder](https://github.com/robotastic/trunk-recorder) (configured for simpleStream with JSON)  
* [ffmpeg](https://ffmpeg.org/) (for encoding and streaming the script's output)

## **trunk-recorder Configuration**

To use this script, you must enable audioStreaming, and enable the simpleStream plugin in your trunk-recorder config.json. This example enables JSON-based streaming of all channels and all systems to the script's UDP port:


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

## **Usage: Quadraphonic (5.1) Streamer**

The Python script's 4-channel audio is piped to ffmpeg, which encodes it as a 5.1 Dolby Digital (AC-3) stream and broadcasts it over UDP to your media player (such as Kodi).

* **Python Script:** simplestream-quad-audio-mixer.py  
* **ffmpeg:** Encodes to AC-3, maps 4 channels to 5.1 layout, streams via UDP.  
* **Kodi:** Listens on UDP port 1234 via a .strm file.

### **Step 1: Run the Streamer**

Run this command on your server. Replace YOUR\_KODI\_PI\_IP with the IP of your Kodi device.  

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide_banner \
        -loglevel warning \
        -f s16le -ar 8000 -ac 4 -channel_layout quad -i pipe:0 \
        -af "pan=5.1|c0=c0|c1=c1|c4=c2|c5=c3" \
        -c:a ac3 -b:a 448k \
        -f mpegts \
        udp://YOUR\_KODI_PI_IP:1234

### **Step 2: Create .strm file on Kodi**

Create a file named radio.strm on your Kodi device with the following content:  

    udp://@:1234

Playing this file in Kodi will start the stream. Your amp should indicate a Dolby Digital signal.

## **Alternative Usage: Local Stereo Playback**

If you don't have a 5.1 system and just want to listen to the mixed audio in stereo on your local machine (via PulseAudio or PipeWire), you can use this command.  
This command pipes the 4-channel quadraphonic audio from the script into ffmpeg, which then mixes it down to stereo (FL+RL \-\> L, FR+RR \-\> R) and plays it on your default speakers.  

    python3 simplestream-quad-audio-mixer.py | ffmpeg \
        -hide\_banner \
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

