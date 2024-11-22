install dependencies for pyaudio:
sudo apt install portaudio19-dev python3-dev libcap-dev

install requirements with:
pip install -r requirements.txt

Or manually with:
pip install opencv-python opencv-python-headless pygame pyaudio numpy picamera2

to run:
python script.py <Device_2_IP> <Port>
python script.py <Device_B_IP> 5000 6000 <- its this one

streamerAlt works a bit better, keeping all the cams open at all time for smoother transitions (otherwise theres a straight up minute long delay when swapping cams and thats on my fat fuck of a desktop)

streamerAlt has issues where it does not stream the local front cam when the toggleOverlay is true, and it does the overlay when either values are true instead of when both are true
streamerAlt2 is pretty dogshit rn you might want to use streamerAlt as a base instead

streamerAltOverlayManual fixes the issue with the front cam not switching, but it renders the wrong back cam sadly.

streamerAltOVerlay3 is built off of manual in an attempt to fix the back cam issues
streamerAltOVerlay3 works but has massive performance issues
streamer4 is created in an attempt to fix these issues

streamer4 works, but its real tiny,
creating streamer5 to rescale the resolution and go fullscreen

streamer5 works, no audio tho,
gotta add audio.
trying out a fix on streamer6

streamer6 has functioning audio,
attempting to add mic switching on streamer7

So on streamer 7, it uses mic index 0 for the front mic and mic index 1 for the back mic, it checks and switches automatically on get_audio_stream()

on streamer8, we attempt to add data streaming for float arrays
works, has some issues with the indentation and nomenclature but it has a function to send float arrays

streamer9 we attempt to implement chroma keying to back cam

streamer10 has unfinished chroma key code, BUT we have an attempt at using a single thread for video streaming, and swapping which camera to stream based on overlay_status
REQUIRES TESTING 11/17/2024 TEST IT TEST IT TEST IT
streamer10 FUCKING WORKS AND SIGNIFICANTLY IMPROVES PERFORMANCE YESSIR MILLER!!!!!!!!

streamer11 we attempt at implementing eye detection
the test script works under the test folder, shouldnt be too complicated to import and implement
see https://itsourcecode.com/free-projects/opencv/eye-detection-opencv-python-with-source-code/ for details
streamer11 works, but low res affects eye detection, gonna try improving in streamer12

streamer 12 somewhat improves eye detection, with some performance issues, theres some rescaling to do to fit the device camera quality, 
and also we might not want to link eye detection to overlay_status, whatever
