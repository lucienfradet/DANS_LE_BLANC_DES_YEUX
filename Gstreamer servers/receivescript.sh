#!/bin/bash

gst-launch-1.0 -v udpsrc port=5000 caps="application/x-rtp, \
media=video, encoding-name=H264, payload=96" ! \
    rtph264depay ! h264parse ! tee name=t \
    t. ! queue ! avdec_h264 ! videoconvert ! autovideosink sync=false \
    t. ! queue ! mp4mux ! filesink location=recorded_video.mp4
