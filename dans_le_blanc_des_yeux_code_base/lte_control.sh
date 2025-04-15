#!/bin/bash
# LTE HAT control script for Raspberry Pi 5
# Define the GPIO pin number
PIN=22
CHIP=0  # gpiochip0 as seen in your gpioinfo output

# Function to set up the GPIO using the newer gpiod interface
setup_gpio() {
  echo "Setting up GPIO..."
  # Check if the pin is already exported by our script
  if [ -e /dev/gpiochip$CHIP ]; then
    # Configure the pin as output if not already configured
    if ! gpioget gpiochip$CHIP $PIN &>/dev/null; then
      gpioset --mode=signal gpiochip$CHIP $PIN=0
    fi
  else
    echo "Error: GPIO chip not found!"
    exit 1
  fi
}

# Function to turn ON the LTE HAT
power_on() {
  echo "Turning ON LTE HAT..."
  gpioset gpiochip$CHIP $PIN=1
}

# Function to turn OFF the LTE HAT
power_off() {
  echo "Turning OFF LTE HAT..."
  gpioset gpiochip$CHIP $PIN=0
}

# Function to get status
get_status() {
  STATUS=$(gpioget gpiochip$CHIP $PIN 2>/dev/null)
  if [ "$?" -eq 0 ]; then
    if [ "$STATUS" -eq "1" ]; then
      echo "LTE HAT is ON"
    else
      echo "LTE HAT is OFF"
    fi
  else
    echo "GPIO pin not configured or error reading status"
  fi
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
    get_status
    ;;
  *)
    echo "Usage: $0 {on|off|status}"
    exit 1
    ;;
esac
exit 0
