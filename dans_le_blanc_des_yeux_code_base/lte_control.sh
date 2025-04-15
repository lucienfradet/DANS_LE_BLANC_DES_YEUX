#!/bin/bash

# Define the GPIO pin number (BCM 22 / D6)
PIN=22

# Function to set up the GPIO
setup_gpio() {
  # Export the GPIO pin if not already exported
  if [ ! -e /sys/class/gpio/gpio$PIN ]; then
    echo $PIN > /sys/class/gpio/export
    # Give the system time to set up the pin
    sleep 0.1
  fi
  
  # Set GPIO direction to output
  echo "out" > /sys/class/gpio/gpio$PIN/direction
}

# Function to turn ON the LTE HAT
power_on() {
  echo "Turning ON LTE HAT..."
  echo "1" > /sys/class/gpio/gpio$PIN/value
}

# Function to turn OFF the LTE HAT
power_off() {
  echo "Turning OFF LTE HAT..."
  echo "0" > /sys/class/gpio/gpio$PIN/value
}

# Function to clean up (unexport the GPIO)
cleanup() {
  echo "Cleaning up GPIO..."
  echo $PIN > /sys/class/gpio/unexport
}

# Main script execution
case "$1" in
  on)
    setup_gpio
    power_on
    ;;
  off)
    setup_gpio
    power_off
    ;;
  status)
    if [ -e /sys/class/gpio/gpio$PIN/value ]; then
      value=$(cat /sys/class/gpio/gpio$PIN/value)
      if [ "$value" -eq "1" ]; then
        echo "LTE HAT is ON"
      else
        echo "LTE HAT is OFF"
      fi
    else
      echo "GPIO pin not configured"
    fi
    ;;
  cleanup)
    cleanup
    ;;
  *)
    echo "Usage: $0 {on|off|status|cleanup}"
    exit 1
    ;;
esac

exit 0
