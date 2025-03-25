import os
import gi
import threading
import time
from shared_variables import config, local_osc, received_osc

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GLib, GstApp, GObject

class VideoStreamer:
    def __init__(self):
        # Initialize GStreamer
        Gst.init(None)
        
        # Get IP from config
        self.receiver_ip = config['ip']['pi-ip']
        
        # Stream state
        self.streaming = False
        self.receiving = False
        self.main_loop = None
        self.pipeline = None
        self.receiver_pipeline = None
        
        # Threading
        self.loop_thread = None
        
    def start_main_loop(self):
        """Start GLib main loop in a separate thread"""
        self.main_loop = GLib.MainLoop()
        self.loop_thread = threading.Thread(target=self.main_loop.run)
        self.loop_thread.daemon = True
        self.loop_thread.start()
        
    def stop_main_loop(self):
        """Stop the GLib main loop"""
        if self.main_loop and self.main_loop.is_running():
            self.main_loop.quit()
            self.loop_thread.join(timeout=1.0)
    
    def create_sender_pipeline(self):
        """Create pipeline for sending video"""
        # Create pipeline using libcamera for Raspberry Pi camera
        pipeline_str = f"""
            libcamera-source ! video/x-raw,width=640,height=480,framerate=30/1 !
            videoconvert ! x264enc tune=zerolatency bitrate=500 speed-preset=superfast ! 
            rtph264pay config-interval=1 pt=96 ! 
            udpsink host={self.receiver_ip} port=5000
        """
        self.pipeline = Gst.parse_launch(pipeline_str)
        
        # Add watch for messages
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        
        return self.pipeline
    
    def create_receiver_pipeline(self):
        """Create pipeline for receiving video"""
        pipeline_str = """
            udpsrc port=5000 caps="application/x-rtp, media=video, encoding-name=H264, payload=96" !
            rtph264depay ! h264parse ! avdec_h264 !
            videoconvert ! appsink name=appsink emit-signals=true
        """
        self.receiver_pipeline = Gst.parse_launch(pipeline_str)
        
        # Get appsink element
        self.appsink = self.receiver_pipeline.get_by_name("appsink")
        self.appsink.set_property("emit-signals", True)
        self.appsink.connect("new-sample", self.on_new_sample)
        
        # Add watch for messages
        bus = self.receiver_pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        
        return self.receiver_pipeline
    
    def start_streaming(self):
        """Start streaming video to the other device"""
        if not self.streaming:
            if not self.main_loop:
                self.start_main_loop()
                
            self.create_sender_pipeline()
            self.pipeline.set_state(Gst.State.PLAYING)
            self.streaming = True
            print("Started streaming video")
    
    def stop_streaming(self):
        """Stop streaming video"""
        if self.streaming and self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.streaming = False
            print("Stopped streaming video")
    
    def start_receiving(self):
        """Start receiving video from the other device"""
        if not self.receiving:
            if not self.main_loop:
                self.start_main_loop()
                
            self.create_receiver_pipeline()
            self.receiver_pipeline.set_state(Gst.State.PLAYING)
            self.receiving = True
            print("Started receiving video")
    
    def stop_receiving(self):
        """Stop receiving video"""
        if self.receiving and self.receiver_pipeline:
            self.receiver_pipeline.set_state(Gst.State.NULL)
            self.receiving = False
            print("Stopped receiving video")
    
    def on_message(self, bus, message):
        """Handle GStreamer pipeline messages"""
        t = message.type
        if t == Gst.MessageType.EOS:
            print("End of stream")
            if self.streaming:
                self.stop_streaming()
            if self.receiving:
                self.stop_receiving()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error: {err.message}, {debug}")
            if self.streaming:
                self.stop_streaming()
            if self.receiving:
                self.stop_receiving()
    
    def on_new_sample(self, sink):
        """Process new video frames from receiver"""
        sample = sink.emit("pull-sample")
        if sample:
            # Here you would pass the frame to display.py or any other processing
            # For now, just print that we received a frame
            # print("Received video frame")
            
            # In a real implementation, you would:
            # buffer = sample.get_buffer()
            # caps = sample.get_caps()
            # Convert buffer to numpy array and process
            pass
        return Gst.FlowReturn.OK
    
    def cleanup(self):
        """Clean up resources"""
        if self.streaming:
            self.stop_streaming()
        if self.receiving:
            self.stop_receiving()
        self.stop_main_loop()

# Singleton instance
video_streamer = None

def get_video_streamer():
    """Get or create the VideoStreamer instance"""
    global video_streamer
    if video_streamer is None:
        video_streamer = VideoStreamer()
    return video_streamer

def run_streamer():
    """Main function to run the video streamer"""
    streamer = get_video_streamer()
    
    try:
        # This would be integrated with your state management
        # For now, just start in a test mode
        print("Starting video streamer in test mode...")
        
        # Uncomment to test streaming
        # streamer.start_streaming()
        
        # Uncomment to test receiving
        # streamer.start_receiving()
        
        # Keep the script running
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("Stopping video streamer...")
    finally:
        streamer.cleanup()

if __name__ == "__main__":
    run_streamer()
