import configparser

from osc_handler import run_osc_handler

config = configparser.ConfigParser()
config.read('config.ini')

if __name__ == "__main__":
    run_osc_handler()
