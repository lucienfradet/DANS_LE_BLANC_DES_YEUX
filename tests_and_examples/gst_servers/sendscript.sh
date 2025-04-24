#!/bin/bash

# Replace <RECEIVER_IP> with the IP address of the Receiver Pi
RECEIVER_IP="100.122.183.98"  # Replace with your Receiver Pi's IP address, was 100.64.244.127

# Capture video with libcamera-vid and pipe it to GStreamer
libcamera-vid -t 0 --codec h264 --inline --flush --libav-format mpegts -o - | \
gst-launch-1.0 -v fdsrc ! \
    tsdemux ! h264parse ! rtph264pay config-interval=1 pt=96 ! \
    udpsink host=$RECEIVER_IP port=5000

