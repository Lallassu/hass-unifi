# Home Assistant Person Detection

This project analyzes chunked videos from Unifi NVR system and detects
persons in the videos. The detections are saved to a Sqlite3 database and 
snapshots to disk. On each detection a very basic html page is generated 
to show latest detections.

Each detection triggers an alarm that is posted on an MQTT queue. One post to 
an motion queue and one alarm to a image queue that is configured in HASS to show
push notifications and sending emails with the detected image.

The detection is performed using OpenCV in combination with YOLOv3.

Weights is downloaded here (download yolov3.weights):
https://github.com/ultralytics/yolov3/releases

** Note that this has been implemented to support cameras of older versions that lacks person detection from Unifi. Should also work with any camera that produces video files **

## Detection
This works rather fast on low-scale platforms such as old laptops etc. It may have some 
miss-matches but usually works really well.

It has been developed to be used with low end platforms, hence it might require some 
tweaking to work faster/better precision with more resourceful platforms.

The detection is performed in the following way:
1. Traverse all video chunks the last day per camera
2. If already in database, skip.
3. If not in database, perform detection every 15 frame (cofigurable), to match 1 frame/sec in the video.
4. If a match, it will skip detection of the rest of the videos in that directory (for that camera). But it will mark the rest of the files as checked. This is to improve performance and only larm once and perform detection once per set of chunks.
5. If a person is detected. The image is saved to the database and a simple webpage is generated with the last <configured> snapshots.
 
## Installation
You can use the service file and enable it in systemd. Or just run it as is. Setup the `config.ini` with the configuration for your system. Make sure python3 and all used libraries are installed. 

## Hass Example Configuration
HASS MQTT Configuration (configuration.yaml):
```
mqtt:
  broker: 127.0.0.1
  username: username
  password: changeme
  port: 1883

binary_sensor:
  - platform: mqtt
    name: "person_motion"
    state_topic: "yolo/camera/motion"

```

HASS Automation for alarm push notification and email with email (automation.yaml):
```
- id: alarmpush
  trigger:
    platform: mqtt
    topic: "yolo/camera/motion"
  condition:
     conditions:
         - condition: state
           entity_id: binary_sensor.person_motion
           state: 'on'
  action:
    - service: notify.push
      data_template:
         title: "Motion Detected"
         message: "Person detected on camera."
         data:
            image: 'https://yourhass.com/local/predictions.jpg?v={{ (range(1, 100000) | random) }}'
            url: 'https://yourhass.com/local/predictions.jpg?v={{ (range(1, 100000) | random) }}'
    - service: notify.email
      data:
          title: 'Motion Detected'
          message: ''
          data:
              images:
                  - /config/www/predictions.jpg

```
