from __future__ import print_function
import argparse
import numpy as np
import cv2
import time
from prettytable import PrettyTable
import csv
import pandas as pd

import keras
from keras_retinanet import models

# Set tf backend to allow memory to grow, instead of claiming everything
import tensorflow as tf
import os
import sys
sys.path.append(os.path.join(".."))

from utils.ncoverage import NCoverage
import utils.dataframe as df
import utils.retinanet as retinanet
import utils.augmentation.augment as aug
import utils.evaluate as eval


def compare_coverage(ncdict, ncdict_aug):
    similar = 0
    for key in ncdict.keys():
        if ncdict[key] == ncdict_aug[key] == True:
            similar += 1
    
    return similar


def cnn_coverage(data_path, aug_path, weights_path, classes, results, nc_threshold):
    # nc_threshold = 0.75
    iou_threshold = 0.5
    score_threshold = 0.3

    # Model build + init variables
    # ---------------------------------------------------------------------------------
    keras.backend.tensorflow_backend.set_session(retinanet.get_session())
    # Models can be downloaded here: https://github.com/fizyr/keras-retinanet/releases
    model = models.load_model(weights_path, backbone_name='resnet50')
    # print(model.summary())
    
    i = 0 # To keep track over number of iterations (want to skip last iteration)
    i_csv = 1 # CSV is one-indexed
    total_tp, total_fp, total_fn = 0, 0, 0
    NCs, NCs_updated = [], []
    res_print = PrettyTable(['Name', 'TP', 'FP', 'FN', 'Precision', 'Recall', 'Neuron coverage', 'Coverage similarity', 'Increase coverage'])
    name, precision, recall, p, similarity, increase = '-', '-', '-', '-', '-', '-'
    res_save = pd.DataFrame(columns=['name', 'TP', 'FP', 'FN', 'precision', 'recall', 'ncoverage', 'coverage_similarity', 'increase_coverage'])     # CSV for saving
    nc = NCoverage(model, nc_threshold)    

    NC_save = pd.DataFrame(columns=['Threshold', 'NC_avg', 'NC_avg_updated'])

    # Extract data
    # ---------------------------------------------------------------------------------
    gt_dict = df.read(data_path)

    try:
        for img, data in gt_dict.items():
            print("\n---New iteration. Image: ", img, "---")

            # If previous results exists we want to continue from last saved iteration
            if results is not None and os.path.exists(results):
                existance, i_csv, total_tp, total_fp, total_fn = df.exists(results, img)
                if existance:
                    print("Iteration", i, "-", img, "is previously analysed. Results can be found at", results)
                    i += 1
                    continue # Skip til next iteration

                precision_total = eval.calculate_precision(total_tp, total_fp, total_fn)
                recall_total = eval.calculate_recall(total_tp, total_fp, total_fn)

                res_print.add_row(["PREVIOUS: ", total_tp, total_fp, total_fn, precision_total, recall_total, '-', '-', '-'])
                print("\n", res_print)
            

            name, precision, recall, p, similarity, increase = img, '-', '-', '-', '-', '-'
            sum_tp, sum_fp, sum_fn = 0, 0, 0

            image_path = os.path.join(data_path, img)
            image, draw, scale = retinanet.load_image(image_path)

            # Predict
            # --------------------------------------------------------------------------------
            start = time.time()
            bboxes, scores, labels = model.predict(np.expand_dims(image, axis=0))
            bboxes /= scale

            # Non-max suppress more strictly
            bboxes, scores, labels = eval.nms(scores, bboxes, labels, score_threshold, iou_threshold)
            print("processing time: ", time.time() - start)

            # Format boundingboxes as {object, [[x1,y1,x2,y2],[x1,y1,x2,y2],...]}
            gt_boxes = df.extract_gt(data)
            pred_boxes = df.format_pred_bb(bboxes, scores, labels, classes, score_threshold)

            precision, recall, pn_classification = eval.calculate_precision_recall(pred_boxes, gt_boxes, iou_threshold)
            sum_tp += pn_classification['true_pos']
            sum_fp += pn_classification['false_pos']
            sum_fn += pn_classification['false_neg']

            # Display/save detections
            image = retinanet.insert_detections(draw, bboxes, scores, labels, classes, score_threshold)
            retinanet.save_to_dir(data_path, name, image)
            # retinanet.display_image(image)


            # Evaluate NC
            # --------------------------------------------------------------------------------
            print("\n---Update neuron coverage---")
            ncdict = nc.update_coverage(np.expand_dims(image, axis=0))
            covered_org, total_org, p = nc.curr_neuron_cov()
            nc_combined = nc

            # Save and print results as you go
            res_save.loc[i_csv] = [name, pn_classification['true_pos'], pn_classification['false_pos'], pn_classification['false_neg'], precision, recall, p, similarity, increase]
            i_csv += 1

            res_print.add_row([name, pn_classification['true_pos'], pn_classification['false_pos'], pn_classification['false_neg'], precision, recall, p, similarity, increase])
            print("\n", res_print)

            NCs.append(p)

            for aug_dir, _, aug_files in os.walk(aug_path): # returns (subdir, dirs (list), files (list))
                img_name = img.split('.')[0] + '.jpg'
                if aug_dir != aug_path and img_name in aug_files:
                    print("\n---Analysing augmented picture,", os.path.basename(aug_dir), ", of", img_name, "---")
                    name, precision, recall, p, similarity, increase = (img.split('.')[0] + "-" +  os.path.basename(aug_dir)), '-', '-', '-', '-', '-'
                    precision_combined, recall_combined, p_combined, increase = '-', '-', '-', '-'
                    
                    image_path = os.path.join(aug_dir, img_name)
                    aug_image, draw, scale = retinanet.load_image(image_path)


                    # Predict
                    # --------------------------------------------------------------------------------
                    start = time.time()
                    bboxes, scores, labels = model.predict(np.expand_dims(aug_image, axis=0))
                    bboxes /= scale

                    # Non-max suppress more strictly
                    bboxes, scores, labels = eval.nms(scores, bboxes, labels, score_threshold, iou_threshold)
                    print("processing time: ", time.time() - start)

                    # Format boundingboxes as {object, [[x1,y1,x2,y2],[x1,y1,x2,y2],...]}
                    gt_boxes = df.extract_gt(data)
                    pred_boxes = df.format_pred_bb(bboxes, scores, labels, classes, score_threshold)
                    
                    # Evaluate precision and recall
                    precision, recall, pn_classification = eval.calculate_precision_recall(pred_boxes, gt_boxes, iou_threshold)
                    sum_tp += pn_classification['true_pos']
                    sum_fp += pn_classification['false_pos']
                    sum_fn += pn_classification['false_neg']

                    # Display/save detections
                    aug_image = retinanet.insert_detections(draw, bboxes, scores, labels, classes, score_threshold)
                    retinanet.save_to_dir(data_path, name + ".jpg", aug_image)
                    # retinanet.display_image(aug_image)


                    # Evaluate NC
                    # --------------------------------------------------------------------------------
                    if nc.is_testcase_increase_coverage(np.expand_dims(aug_image, axis=0)):
                        print("Augmented image increases coverage and should be added to population.")
                        increase = True
                    else: increase = False

                    print("\n---Update neuron coverage with augmented image---")
                    nc_aug = NCoverage(model, nc_threshold)
                    ncdict_aug = nc_aug.update_coverage(np.expand_dims(aug_image, axis=0)) # This may be done on a new nc object
                    covered, total, p = nc_aug.curr_neuron_cov()

                    similar_neurons = compare_coverage(ncdict, ncdict_aug)
                    similarity = similar_neurons / total

                    print("\n---Update combined neuron coverage for all augmentations of image", img, "---")
                    nc_combined.update_coverage(np.expand_dims(aug_image, axis=0))


                    # Save and print results as you go
                    res_save.loc[i_csv] = [name, pn_classification['true_pos'], pn_classification['false_pos'], pn_classification['false_neg'], precision, recall, p, similarity, increase]  
                    i_csv += 1   
                    res_print.add_row([name, pn_classification['true_pos'], pn_classification['false_pos'], pn_classification['false_neg'], precision, recall, p, similarity, increase])
                    print("\n", res_print)


            print("---Calculating sum for", img, "---")
            precision_combined = eval.calculate_precision(sum_tp, sum_fp, sum_fn)
            recall_combined = eval.calculate_recall(sum_tp, sum_fp, sum_fn)
    
            covered_combined, total_combined, p_combined = nc_combined.curr_neuron_cov()
            increase = (covered_combined-covered_org)/covered_org
            res_print.add_row(["COMBINED: ", sum_tp, sum_fp, sum_fn, precision_combined, recall_combined, p_combined, "-", increase])

            total_tp += sum_tp
            total_fp += sum_fp
            total_fn += sum_fn
            sum_tp, sum_fp, sum_fn = 0, 0, 0 # To make sure sum isn't part of total when already added

            NCs_updated.append(p_combined)

            # Check if last iteration
            i += 1
            if i <= len(gt_dict):
                print("\n---New image. Reset coverage dictionary---")
                nc.reset_cov_dict() 

    except KeyboardInterrupt:
        res_print.add_row([name, pn_classification['true_pos'], pn_classification['false_pos'], pn_classification['false_neg'], precision, recall, p, similarity, increase]) # Add last result if aborted
        pass

    precision_total = eval.calculate_precision(total_tp + sum_tp, total_fp + sum_fp, total_fn + sum_fn)
    recall_total = eval.calculate_recall(total_tp + sum_tp, total_fp + sum_fp, total_fn + sum_fn)

    # Creates class file
    if results is not None and os.path.exists(results):
        res_save.to_csv(path_or_buf=results, mode='a', header=False, index=False, index_label=False)
    else:
        res_save.to_csv(path_or_buf=os.path.join(data_path, os.pardir, 'results_one.csv'), header=False, index=False, index_label=False)
    
    res_print.add_row(["TOTAL: ", total_tp + sum_tp, total_fp + sum_fp, total_fn + sum_fn, precision_total, recall_total, '-', '-', '-'])
    print("\n", res_print)

    try:
        NC_avg = sum(NCs) / len(NCs)
        print("Average neuron coverage over non-augmented images with nc_threshold = ", nc_threshold, ": ", NC_avg)

        NC_updated_avg = sum(NCs_updated) / len(NCs_updated)    
        print("Average neuron coverage including augmented images with nc_threshold = ", nc_threshold, ": ", NC_updated_avg)
    

        NC_save.loc[nc_threshold*10] = [nc_threshold, NC_avg, NC_updated_avg]
        if os.path.exists("data/NC.csv"):
            NC_save.to_csv(path_or_buf="data/NC.csv", mode='a', header=False, index=False, index_label=False)
        else:
            NC_save.to_csv(path_or_buf="data/NC.csv", header=False, index=False, index_label=False)
    
    except ZeroDivisionError:
        pass


    
    


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset', type=str, help='Path to dataset.')
    parser.add_argument('--weights', type=str, default=os.path.join(os.pardir, 'keras-retinanet/snapshots', 'resnet50_csv_04_inference.h5'),
                        help='Path to weights. Should be added to ../keras-retinanet/snapshots')
    parser.add_argument('--classes', type=str,
                        help='Path to csv file containing classes.')
    parser.add_argument('--results', type=str,
                        help='Path to csv file containing previous results.')
    args = parser.parse_args()

    try:
        classes = df.read_classes(args.classes)
    except TypeError:
        # classes = {0: 'person', 1: 'bicycle', 2: 'car', 3: 'motorcycle', 4: 'airplane', 5: 'bus', 6: 'train', 7: 'truck', 8: 'boat', 9: 'traffic light', 10: 'fire hydrant', 11: 'stop sign', 12: 'parking meter', 13: 'bench', 14: 'bird', 15: 'cat', 16: 'dog', 17: 'horse', 18: 'sheep', 19: 'cow', 20: 'elephant', 21: 'bear', 22: 'zebra', 23: 'giraffe', 24: 'backpack', 25: 'umbrella', 26: 'handbag', 27: 'tie', 28: 'suitcase', 29: 'frisbee', 30: 'skis', 31: 'snowboard', 32: 'sports ball', 33: 'kite', 34: 'baseball bat', 35: 'baseball glove', 36: 'skateboard', 37: 'surfboard', 38: 'tennis racket', 39: 'bottle', 40: 'wine glass', 41: 'cup', 42: 'fork', 43: 'knife', 44: 'spoon', 45: 'bowl', 46: 'banana', 47: 'apple', 48: 'sandwich', 49: 'orange', 50: 'broccoli', 51: 'carrot', 52: 'hot dog', 53: 'pizza', 54: 'donut', 55: 'cake', 56: 'chair', 57: 'couch', 58: 'potted plant', 59: 'bed', 60: 'dining table', 61: 'toilet', 62: 'tv', 63: 'laptop', 64: 'mouse', 65: 'remote', 66: 'keyboard', 67: 'cell phone', 68: 'microwave', 69: 'oven', 70: 'toaster', 71: 'sink', 72: 'refrigerator', 73: 'book', 74: 'clock', 75: 'vase', 76: 'scissors', 77: 'teddy bear', 78: 'hair drier', 79: 'toothbrush'}
        classes = {0: 'motor_vessel', 1: 'sailboat_sail', 2: 'sailboat_motor', 3: 'kayak'}

    print("\n---Augmenting---")
    aug_save = os.path.join(args.dataset, os.pardir, 'augmented')
    if not os.path.exists(aug_save):
        os.makedirs(aug_save)  

    aug.augment(args.dataset, aug_save)
    
    print("\n---Starting test---")
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.1)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.2)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.3)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.4)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.5)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.6)
    cnn_coverage(args.dataset, aug_save, args.weights, classes, args.results, 0.7)

