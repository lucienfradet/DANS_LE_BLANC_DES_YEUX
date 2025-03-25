import configparser

received_osc = {"y": 0, "z": 0, "pressure": True}
local_osc = {"y": 0, "z": 0, "pressure": False}

def update_local_osc(parsed_data):
    global local_osc
    local_osc["y"] = parsed_data["y"]
    local_osc["z"] = parsed_data["z"]
    local_osc["pressure"] = parsed_data["pressure"]

def update_recieved_osc(parsed_data):
    global received_osc
    received_osc["y"] = parsed_data["y"]
    received_osc["z"] = parsed_data["z"]
    received_osc["pressure"] = parsed_data["pressure"]

config = configparser.ConfigParser()
config.read('config.ini')
