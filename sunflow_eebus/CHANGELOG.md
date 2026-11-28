# Changelog

## 0.1.1

- The API for Sunflow needs the token Sunflow shows when pairing (option `token`); without it nothing is answered, so no other App on the Supervisor's network can steer a wallbox.
- A wallbox in another network segment (which mDNS does not reach) can be trusted with its IP and port: the App announces it in its own segment, so the EEBUS search connects to that address.

## 0.1.0

- First version: Sunflow as energy manager (CEM) for EEBUS wallboxes with the use cases EV commissioning (EVCC), EV measurement (EVCEM), current limits (OPEV) and EV state of charge (EVSOC), built on eebus-go. Not verified on hardware yet.
