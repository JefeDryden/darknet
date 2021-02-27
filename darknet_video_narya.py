from ctypes import *
import random
import os
import cv2
import time
import darknet
import argparse
from threading import Thread, enumerate
from queue import Queue
from narya.narya.utils.homography import compute_homography, warp_image,warp_point, get_perspective_transform
from narya.narya.utils.image import torch_img_to_np_img, np_img_to_torch_img, denormalize
from narya.narya.utils.utils import to_torch
from narya.narya.utils.vizualization import merge_template, visualize, rgb_template_to_coord_conv_template
from narya.narya.models.keras_models import KeypointDetectorModel
from narya.narya.utils.masks import _points_from_mask 
import numpy as np
import keras
from keras.models import load_model


import rink_image_drawer

def parser():
    parser = argparse.ArgumentParser(description="YOLO Object Detection")
    parser.add_argument("--input", type=str, default=0,
                        help="video source. If empty, uses webcam 0 stream")
    parser.add_argument("--out_filename", type=str, default="",
                        help="inference video name. Not saved if empty")
    parser.add_argument("--weights", default="yolov4.weights",
                        help="yolo weights path")
    parser.add_argument("--dont_show", action='store_true',
                        help="windown inference display. For headless systems")
    parser.add_argument("--ext_output", action='store_true',
                        help="display bbox coordinates of detected objects")
    parser.add_argument("--config_file", default="./cfg/yolov4.cfg",
                        help="path to config file")
    parser.add_argument("--data_file", default="./cfg/coco.data",
                        help="path to data file")
    parser.add_argument("--thresh", type=float, default=.25,
                        help="remove detections with confidence below this value")
    return parser.parse_args()


def str2int(video_path):
    """
    argparse returns and string althout webcam uses int (0, 1 ...)
    Cast to int if needed
    """
    try:
        return int(video_path)
    except ValueError:
        return video_path


def check_arguments_errors(args):
    assert 0 < args.thresh < 1, "Threshold should be a float between zero and one (non-inclusive)"
    if not os.path.exists(args.config_file):
        raise(ValueError("Invalid config path {}".format(os.path.abspath(args.config_file))))
    if not os.path.exists(args.weights):
        raise(ValueError("Invalid weight path {}".format(os.path.abspath(args.weights))))
    if not os.path.exists(args.data_file):
        raise(ValueError("Invalid data file path {}".format(os.path.abspath(args.data_file))))
    if str2int(args.input) == str and not os.path.exists(args.input):
        raise(ValueError("Invalid video path {}".format(os.path.abspath(args.input))))


def set_saved_video(input_video, output_video, size):
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    fps = int(input_video.get(cv2.CAP_PROP_FPS))
    video = cv2.VideoWriter(output_video, fourcc, fps, size)
    return video


def video_capture(frame_queue, darknet_image_queue):
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_resized = cv2.resize(frame_rgb, (width, height),
                                   interpolation=cv2.INTER_LINEAR)
        frame_queue.put(frame_resized)
        img_for_detect = darknet.make_image(width, height, 3)
        darknet.copy_image_from_bytes(img_for_detect, frame_resized.tobytes())
        darknet_image_queue.put(img_for_detect)
    cap.release()


def inference(darknet_image_queue, detections_queue, fps_queue):

    while cap.isOpened():
        darknet_image = darknet_image_queue.get()
        prev_time = time.time()
        detections = darknet.detect_image(network, class_names, darknet_image, thresh=args.thresh)
        detections_queue.put(detections)        
        fps = int(1/(time.time() - prev_time))
        fps_queue.put(fps)
        print("FPS: {}".format(fps))
        darknet.print_detections(detections, args.ext_output)
        darknet.free_image(darknet_image)
    cap.release()



def drawing(frame_queue, detections_queue, fps_queue):
    random.seed(3)  # deterministic bbox colors
    
    imgNum = 0
    img = cv2.imread(r'/darknet/RinkModelV2.png',1)
    rinkHeight = 720
    rinkWidth = 1280
    capHeight = 720
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (1100,466))/255.
    img = rgb_template_to_coord_conv_template(img)
    kp_model = KeypointDetectorModel(backbone='efficientnetb3', num_classes=32, input_shape=(320, 320), )
    kp_model.load_weights(r"/mydrive/Models/keypoint_hockey_weights15.h5")
    
    wRatio = rinkWidth/416
    hRatio = capHeight/416
    detectionList = []
    detectionItem = []
    out = cv2.VideoWriter(r'/mydrive/images/RinkModel.avi',cv2.VideoWriter_fourcc('M','J','P','G'), 30, (rinkWidth,rinkHeight+capHeight))
    
    video = set_saved_video(cap, args.out_filename, (width, height))
    
    while cap.isOpened():
        frame_resized = frame_queue.get()
        detections = detections_queue.get()
        fps = fps_queue.get()

        detectionList = []
        imgNum+=1
        frame_resized = cv2.resize(frame_resized, (rinkWidth,capHeight), interpolation = cv2.INTER_AREA) #custom
        pr_mask = kp_model(frame_resized) #Custom Line of Code
        src,dst = _points_from_mask(pr_mask[0])
        pred_homo = get_perspective_transform(dst,src)
        pred_warp = warp_image(img,pred_homo,out_shape=(320,320))
        
        for detection in detections: #custom
            
            detectionX = detection[2][0]*wRatio
            detectionY = detection[2][1]*hRatio
            detectionW = detection[2][2]*wRatio
            detectionH = detection[2][3]*hRatio
            detectionCoords = (detectionX,detectionY,detectionW,detectionH)
            detectionItem = (detection[0],detection[1],detectionCoords)
            detectionList.append(detectionItem)            
        
        
        if frame_resized is not None:
            image = darknet.draw_boxes(detectionList, frame_resized, class_colors)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            merged = merge_template(image/255.,cv2.resize(pred_warp, (rinkWidth,capHeight)))
            
            
            
            fullImg = cv2.vconcat([image, merged])
            
            if args.out_filename is not None:
                video.write(image)
                out.write(fullImg)
            if not args.dont_show:
                cv2.imshow('Inference', image)
            if cv2.waitKey(fps) == 27:
                break
    out.release() #custom
    cap.release()
    video.release()
    
    cv2.destroyAllWindows()


if __name__ == '__main__':
    frame_queue = Queue()
    darknet_image_queue = Queue(maxsize=1)
    detections_queue = Queue(maxsize=1)
    fps_queue = Queue(maxsize=1)
    args = parser()
    check_arguments_errors(args)
    network, class_names, class_colors = darknet.load_network(
            args.config_file,
            args.data_file,
            args.weights,
            batch_size=1
        )
    width = darknet.network_width(network)
    height = darknet.network_height(network)
    input_path = str2int(args.input)
    cap = cv2.VideoCapture(input_path)
    Thread(target=video_capture, args=(frame_queue, darknet_image_queue)).start()
    Thread(target=inference, args=(darknet_image_queue, detections_queue, fps_queue)).start()
    Thread(target=drawing, args=(frame_queue, detections_queue, fps_queue)).start()
