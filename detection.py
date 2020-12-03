#!/usr/bin/python3
import subprocess
import os
import cv2
import numpy as np
import sys
import time
import sqlite3
import shutil
from jinja2 import Environment, FileSystemLoader
import configparser

config = configparser.ConfigParser()


def dict_factory(cursor, row):
    """
    Helper to create a dict out of the DB rows
    """
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def GeneratePage(conn):
    """
    Generate webpage with detections based on a jinja2 template
    """
    c = conn.cursor()
    c.row_factory = dict_factory
    c.execute("SELECT * FROM detection order by TimeStamp desc")
    detections = c.fetchmany(int(config['HPD']['WebLastDetections']))

    for d in detections:
        d["large"] = os.path.basename(d["ImagePath"])

    file_loader = FileSystemLoader('.')
    env = Environment(loader=file_loader)

    template = env.get_template('template.html')
    output = template.render(detections=detections)
    f = open(os.path.join(config['HPD']['DetectionPath'], "index.html"), "w+")
    f.write(output)
    f.close()


def SaveDetectionToDB(conn, camName, image, videoFile):
    """
    Save detections to database
    """
    c = conn.cursor()
    c.execute("INSERT INTO detection VALUES(?,?,?,?)",
              [camName, image, videoFile, time.strftime("%Y-%m-%d %H:%M:%S")])
    conn.commit()


def CreateTables(conn):
    """
    Database table creation
    """
    c = conn.cursor()

    # Table for detections
    c.execute('''CREATE TABLE IF NOT EXISTS detection (
        camName text,
        ImagePath text,
        VideoPath text,
        Timestamp text
    )''')

    # Table for processed files
    c.execute('''CREATE TABLE IF NOT EXISTS files (
        file text
    )''')

    conn.commit()


def LoadFromDB(conn):
    """
    Load previously processed files from DB
    """
    c = conn.cursor()
    c.row_factory = lambda cursor, row: row[0]
    c.execute("SELECT file FROM files")
    return c.fetchall()


def SaveToDB(conn, name):
    """
    Save processed file to DB
    """
    # Save name to DB (video file)
    c = conn.cursor()
    c.execute("INSERT INTO files VALUES(?)", [name])
    conn.commit()


def DetectInVideo(net, classes, video):
    """
    Detect persons in given video
    """
    count = 0
    cap = cv2.VideoCapture(video)

    while(True):
        count += 1
        ret, image = cap.read()
        if ret is False:
            return False, None

        if count % int(config['HPD']['SkipFrames']) != 0:
            continue

        Width = image.shape[1]
        Height = image.shape[0]

        net.setInput(cv2.dnn.blobFromImage(image, 0.00392, (224, 224), (0, 0, 0), True, crop=False))
        layer_names = net.getLayerNames()
        output_layers = [layer_names[i[0] - 1] for i in net.getUnconnectedOutLayers()]
        outs = net.forward(output_layers)

        class_ids = []
        confidences = []
        boxes = []

        for out in outs:
            for detection in out:
                scores = detection[5:]
                class_id = np.argmax(scores)
                confidence = scores[class_id]
                if confidence > 0.1:
                    center_x = int(detection[0] * Width)
                    center_y = int(detection[1] * Height)
                    w = int(detection[2] * Width)
                    h = int(detection[3] * Height)
                    x = center_x - w / 2
                    y = center_y - h / 2
                    class_ids.append(class_id)
                    confidences.append(float(confidence))
                    boxes.append([x, y, w, h])

        indices = cv2.dnn.NMSBoxes(boxes, confidences, 0.1, 0.1)

        # If detection is found, draw a red rectangle around person.
        for i in indices:
            i = i[0]
            box = boxes[i]
            if class_ids[i] == 0:
                cv2.rectangle(image, (round(box[0]), round(box[1])), (round(box[0]+box[2]), round(box[1]+box[3])), (0, 0, 255), 1)
                return True, image

    return False, None


def SendAlarm(imagePath):
    """
    Send motion alarm on MQTT that is handled by hass
    Both image and motion trigger
    """
    MQTT_BINARY = config['MQTT']['Binary']
    MQTT_HOST = config['MQTT']['Host']
    MQTT_PORT = config['MQTT']['Port']
    MQTT_USER = config['MQTT']['User']
    MQTT_PASS = config['MQTT']['Pass']
    MQTT_Q_IMAGE = config['MQTT']['ImageQueue']
    MQTT_Q_MOTION = config['MQTT']['MotionQueue']
    DETECTION_PATH = config['HPD']['DetectionPath']

    subprocess.call(
        [MQTT_BINARY, "-h", MQTT_HOST, "-p", MQTT_PORT,
         "-u", MQTT_USER, "-P", MQTT_PASS, "-r", "-t", MQTT_Q_IMAGE,
         "-f", imagePath],
        cwd=DETECTION_PATH,
    )
    subprocess.call(
        [MQTT_BINARY, "-h", MQTT_HOST, "-p", MQTT_PORT,
         "-u", MQTT_USER, "-P", MQTT_PASS, "-r", "-t", MQTT_Q_MOTION,
         "-m", "ON"],
        cwd=DETECTION_PATH,
    )


def main():
    """
    Loop forever in camera dirs for new files to process
    """
    config.read(sys.argv[1])

    conn = sqlite3.connect(config['HPD']['DBFile'])
    CreateTables(conn)

    GeneratePage(conn)

    # Keep track of already analysed files
    print("Loading existing files...", flush=True)
    existingFiles = LoadFromDB(conn)
    print("Load complete.", flush=True)

    with open(config['YOLO']['CocoFile'], 'r') as f:
        classes = [line.strip() for line in f.readlines()]

    net = cv2.dnn.readNet(config['YOLO']['YoloWeights'], config['YOLO']['YoloConfig'])

    # Loop forever
    while(True):
        for cam in config['CAMERAS']:
            dirName = config['CAMERAS'][cam]
            detected = False

            # Get any new files in each dir
            # Check if file exists in DB dict.
            now = time.time()
            # 2 days old
            maxDays = 2 * 86400
            for root, subdirs, files in os.walk(dirName):
                skip = False
                for name in subdirs:
                    if (now - os.path.getmtime(os.path.join(root, name))) > maxDays:
                        skip = True
                        break
                if skip:
                    continue
                for videoFile in files:
                    if not videoFile.endswith(".mp4"):
                        continue

                    videoFile = os.path.join(root, videoFile)
                    # Skip any previously analyzed file
                    if videoFile in existingFiles:
                        continue

                    # Skip empty files
                    if os.stat(videoFile).st_size == 0:
                        continue

                    # If not exists, run detection and if no detection was already
                    # found. We only analyse for first finding so we dont
                    # alarm for every part of the recording(s).
                    if detected is False:
                        print("Analyzing video", cam, videoFile, flush=True)
                        t1 = time.perf_counter()
                        result, image = DetectInVideo(net, classes, videoFile)
                        t2 = time.perf_counter()
                        print(f"  - Detection took: {t2 - t1:0.4f} seconds.", flush=True)
                        if result is True:
                            print("  - Motion detection", cam, time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)

                            # save image to disk
                            largeFile = os.path.join(config['HPD']['DetectionPath'], time.strftime("%Y-%m-%d_%H:%M:%S.jpg"))

                            cv2.imwrite(largeFile, image)
                            shutil.copy(largeFile, config['HPD']['LastDetectionFile'])

                            SaveDetectionToDB(conn, cam, largeFile, videoFile)
                            SendAlarm(largeFile)
                            GeneratePage(conn)
                            detected = True

                    sys.stdout.flush()

                    # Save file to DB as analysed.
                    existingFiles.append(videoFile)
                    SaveToDB(conn, videoFile)

        # Perform check with a bit delay
        time.sleep(60)


if __name__ == '__main__':
    main()
