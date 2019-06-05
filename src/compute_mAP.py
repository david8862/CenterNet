#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Calculate mAP for CenterNet model on some annotation dataset
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import os, argparse
#import sys
import numpy as np
import cv2
import skimage.io
from PIL import Image
import operator
import matplotlib.pyplot as plt

import _init_paths
from opts import opts
from datasets.dataset_factory import dataset_factory
from detectors.detector_factory import detector_factory



def prepare_model(opt):
    os.environ['CUDA_VISIBLE_DEVICES'] = opt.gpus_str

    Dataset = dataset_factory[opt.dataset]
    opt = opts().update_dataset_info_and_set_heads(opt, Dataset)
    Detector = detector_factory[opt.task]
    detector = Detector(opt)
    return detector


def predict(model, image_name, score_threshold):
    # Run detection
    ret = model.run(image_name)
    results = ret['results']

    boxes = []
    classes = []
    scores = []

    for i in range(1, model.num_classes + 1):
        for bbox in results[i]:
            if bbox[4] > score_threshold:
                boxes.append(bbox[:4])
                classes.append(i-1)
                scores.append(bbox[4])

    boxes = np.array(boxes, dtype=np.int32)
    classes = np.array(classes)
    scores = np.array(scores)

    return boxes, classes, scores


def get_classes(classes_path):
    classes_file = os.path.expanduser(classes_path)
    with open(classes_path) as f:
        class_names = f.readlines()
    class_names = [c.strip() for c in class_names]
    return class_names


def get_colors(class_names):
    import colorsys
    # Generate colors for drawing bounding boxes.
    hsv_tuples = [(x / len(class_names), 1., 1.)
                  for x in range(len(class_names))]
    colors = list(map(lambda x: colorsys.hsv_to_rgb(*x), hsv_tuples))
    colors = list(
        map(lambda x: (int(x[0] * 255), int(x[1] * 255), int(x[2] * 255)),
            colors))
    np.random.seed(10101)  # Fixed seed for consistent colors across runs.
    np.random.shuffle(colors)  # Shuffle colors to decorrelate adjacent classes.
    np.random.seed(None)  # Reset seed to default.
    return colors


def draw_label(image, text, color, coords):
    font = cv2.FONT_HERSHEY_PLAIN
    font_scale = 1.
    (text_width, text_height) = cv2.getTextSize(text, font, fontScale=font_scale, thickness=1)[0]

    padding = 5
    rect_height = text_height + padding * 2
    rect_width = text_width + padding * 2

    (x, y) = coords

    cv2.rectangle(image, (x, y), (x + rect_width, y - rect_height), color, cv2.FILLED)
    cv2.putText(image, text, (x + padding, y - text_height + padding), font,
                fontScale=font_scale,
                color=(255, 255, 255),
                lineType=cv2.LINE_AA)

    return image

def draw_boxes(image, boxes, classes, scores, class_names, colors, show_score=True):
    if classes is None or len(classes) == 0:
        return image

    for box, cls, score in zip(boxes, classes, scores):
        xmin, ymin, xmax, ymax = box

        class_name = class_names[cls]
        if show_score:
            label = '{} {:.2f}'.format(class_name, score)
        else:
            label = '{}'.format(class_name)
        print(label, (xmin, ymin), (xmax, ymax))
        # if no color info, use black(0,0,0)
        if colors == None:
            color = (0,0,0)
        else:
            color = colors[cls]
        cv2.rectangle(image, (xmin, ymin), (xmax, ymax), color, 1, cv2.LINE_AA)
        image = draw_label(image, label, color, (xmin, ymin))

    return image


def touchdir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def annotation_parse(annotation_file, class_names):
    '''
    parse annotation file to get image dict and ground truth class dict

    image dict would be like:
    annotation_records = {
        '000001.jpg': {'100,120,200,235':'dog', '85,63,156,128':'car', ...},
        ...
    }

    ground truth class dict would be like:
    classes_records = {
        'car': [
                ['00001.jpg','100,120,200,235'],
                ['00002.jpg','85,63,156,128'],
                ...
               ],
        ...
    }
    '''
    with open(annotation_file) as f:
        annotation_lines = f.readlines()
    # shuffle dataset
    np.random.seed(10101)
    #np.random.shuffle(annotation_lines)
    np.random.seed(None)

    annotation_records = {}
    classes_records = {}

    for line in annotation_lines:
        box_records = {}
        image_name = line.split(' ')[0]
        boxes = line.split(' ')[1:]
        for box in boxes:
            #strip box coordinate and class
            class_name = class_names[int(box.split(',')[-1])]
            coordinate = ','.join(box.split(',')[:-1])
            box_records[coordinate] = class_name
            #append or add ground truth class item
            if class_name in classes_records:
                classes_records[class_name].append([image_name, coordinate])
            else:
                classes_records[class_name] = list([[image_name, coordinate]])
        annotation_records[image_name] = box_records

    return annotation_records, classes_records


def transform_gt_record(gt_records, class_names):
    '''
    Transform the Ground Truth records of a image to prediction format, in
    order to show & compare in result pic.

    Ground Truth records is a dict with format:
        {'100,120,200,235':'dog', '85,63,156,128':'car', ...}

    Prediction format:
        (boxes, classes, scores)
    '''
    if gt_records is None or len(gt_records) == 0:
        return [], [], []

    gt_boxes = []
    gt_classes = []
    gt_scores = []
    for (coordinate, class_name) in gt_records.items():
        gt_box = [int(x) for x in coordinate.split(',')]
        gt_class = class_names.index(class_name)

        gt_boxes.append(gt_box)
        gt_classes.append(gt_class)
        gt_scores.append(1.0)

    return np.array(gt_boxes), np.array(gt_classes), np.array(gt_scores)


def get_prediction_class_records(opt, annotation_records, class_names, save_result):
    '''
    Do the predict with Mask RCNN model on annotation images to get predict class dict

    predict class dict would contain image_name, coordinate and score, and
    sorted by score:
    pred_classes_records = {
        'car': [
                ['00001.jpg','94,115,203,232',0.98],
                ['00002.jpg','82,64,154,128',0.93],
                ...
               ],
        ...
    }
    '''
    model = prepare_model(opt)
    pred_classes_records = {}
    for (image_name, gt_records) in annotation_records.items():
        image = Image.open(image_name)
        image_data = np.array(image, dtype='uint8')

        #image = skimage.io.imread(image_name)
        pred_boxes, pred_classes, pred_scores = predict(model, image_name, opt.vis_thresh)
        print('Found {} boxes for {}'.format(len(pred_boxes), image_name))

        if save_result:
            gt_boxes, gt_classes, gt_scores = transform_gt_record(gt_records, class_names)

            result_dir=os.path.join('result','detection')
            touchdir(result_dir)
            colors = get_colors(class_names)
            image_data = draw_boxes(image_data, gt_boxes, gt_classes, gt_scores, class_names, colors=None, show_score=False)
            image_data = draw_boxes(image_data, pred_boxes, pred_classes, pred_scores, class_names, colors)
            Image.fromarray(image_data).save(os.path.join(result_dir, image_name.split(os.path.sep)[-1]))

        # Nothing detected
        if pred_boxes is None or len(pred_boxes) == 0:
            continue

        for box, cls, score in zip(pred_boxes, pred_classes, pred_scores):
            pred_class_name = class_names[cls]
            xmin, ymin, xmax, ymax = box
            coordinate = "{},{},{},{}".format(xmin, ymin, xmax, ymax)

            #append or add predict class item
            if pred_class_name in pred_classes_records:
                pred_classes_records[pred_class_name].append([image_name, coordinate, score])
            else:
                pred_classes_records[pred_class_name] = list([[image_name, coordinate, score]])

    # sort pred_classes_records for each class according to score
    for pred_class_list in pred_classes_records.values():
        pred_class_list.sort(key=lambda ele: ele[2], reverse=True)

    return pred_classes_records


def box_iou(pred_box, gt_box):
    '''
    Calculate iou for predict box and ground truth box
    Param
         pred_box: predict box coordinate
                   (xmin,ymin,xmax,ymax) format
         gt_box: ground truth box coordinate
                 (xmin,ymin,xmax,ymax) format
    Return
         iou value
    '''
    # get intersection box
    inter_box = [max(pred_box[0], gt_box[0]), max(pred_box[1], gt_box[1]), min(pred_box[2], gt_box[2]), min(pred_box[3], gt_box[3])]
    # compute overlap (IoU) = area of intersection / area of union
    pred_area = (pred_box[2] - pred_box[0]) * (pred_box[3] - pred_box[1])
    gt_area = (gt_box[2] - gt_box[0]) * (gt_box[3] - gt_box[1])
    inter_area = (inter_box[2] - inter_box[0]) * (inter_box[3] - inter_box[1])
    union_area = pred_area + gt_area - inter_area
    return 0 if union_area == 0 else float(inter_area) / float(union_area)


def match_gt_box(pred_record, gt_records, iou_threshold=0.5):
    '''
    Search gt_records list and try to find a matching box for the predict box

    Param
         pred_record: with format ['image_file', 'xmin,ymin,xmax,ymax', score]
         gt_records: record list with format
                     [
                      ['image_file', 'xmin,ymin,xmax,ymax', 'usage'],
                      ['image_file', 'xmin,ymin,xmax,ymax', 'usage'],
                      ...
                     ]
         iou_threshold:

         pred_record and gt_records should be from same annotation image file

    Return
         matching gt_record index. -1 when there's no matching gt
    '''
    max_iou = 0.0
    max_index = -1
    #get predict box coordinate
    pred_box = [float(x) for x in pred_record[1].split(',')]

    for i, gt_record in enumerate(gt_records):
        #get ground truth box coordinate
        gt_box = [float(x) for x in gt_record[1].split(',')]
        iou = box_iou(pred_box, gt_box)

        # if the ground truth has been assigned to other
        # prediction, we couldn't reuse it
        if iou > max_iou and gt_record[2] == 'unused' and pred_record[0] == gt_record[0]:
            max_iou = iou
            max_index = i

    # drop the prediction if couldn't match iou threshold
    if max_iou < iou_threshold:
        max_index = -1

    return max_index

def voc_ap(rec, prec):
    """
    --- Official matlab code VOC2012---
    mrec=[0 ; rec ; 1];
    mpre=[0 ; prec ; 0];
    for i=numel(mpre)-1:-1:1
        mpre(i)=max(mpre(i),mpre(i+1));
    end
    i=find(mrec(2:end)~=mrec(1:end-1))+1;
    ap=sum((mrec(i)-mrec(i-1)).*mpre(i));
    """
    rec.insert(0, 0.0)  # insert 0.0 at begining of list
    rec.append(1.0)  # insert 1.0 at end of list
    mrec = rec[:]
    prec.insert(0, 0.0)  # insert 0.0 at begining of list
    prec.append(0.0)  # insert 0.0 at end of list
    mpre = prec[:]
    """
     This part makes the precision monotonically decreasing
      (goes from the end to the beginning)
    """
    # matlab indexes start in 1 but python in 0, so I have to do:
    #   range(start=(len(mpre) - 2), end=0, step=-1)
    # also the python function range excludes the end, resulting in:
    #   range(start=(len(mpre) - 2), end=-1, step=-1)
    for i in range(len(mpre) - 2, -1, -1):
        mpre[i] = max(mpre[i], mpre[i + 1])
    """
     This part creates a list of indexes where the recall changes
    """
    # matlab: i=find(mrec(2:end)~=mrec(1:end-1))+1;
    i_list = []
    for i in range(1, len(mrec)):
        if mrec[i] != mrec[i - 1]:
            i_list.append(i)  # if it was matlab would be i + 1
    """
     The Average Precision (AP) is the area under the curve
      (numerical integration)
    """
    # matlab: ap=sum((mrec(i)-mrec(i-1)).*mpre(i));
    ap = 0.0
    for i in i_list:
        ap += ((mrec[i] - mrec[i - 1]) * mpre[i])
    return ap, mrec, mpre

'''
def voc_ap(rec, prec, use_07_metric=False):
    if use_07_metric:
        # 11 point metric
        ap = 0.
        for t in np.arange(0., 1.1, 0.1):
            if np.sum(rec >= t) == 0:
                p = 0
            else:
                p = np.max(prec[rec >= t])
            ap = ap + p / 11.
    else:
        mrec = np.concatenate(([0.], rec, [1.]))
        mpre = np.concatenate(([0.], prec, [0.]))
        for i in range(mpre.size - 1, 0, -1):
            mpre[i - 1] = np.maximum(mpre[i - 1], mpre[i])
        i = np.where(mrec[1:] != mrec[:-1])[0]
        ap = np.sum((mrec[i + 1] - mrec[i]) * mpre[i + 1])
    return ap, mrec, mpre
'''


def get_rec_prec(true_positive, false_positive, gt_records):
    '''
    Calculate precision/recall based on true_positive, false_positive
    result.
    '''
    cumsum = 0
    for idx, val in enumerate(false_positive):
        false_positive[idx] += cumsum
        cumsum += val

    cumsum = 0
    for idx, val in enumerate(true_positive):
        true_positive[idx] += cumsum
        cumsum += val

    rec = true_positive[:]
    for idx, val in enumerate(true_positive):
        rec[idx] = float(true_positive[idx]) / len(gt_records)

    prec = true_positive[:]
    for idx, val in enumerate(true_positive):
        prec[idx] = float(true_positive[idx]) / (false_positive[idx] + true_positive[idx])

    return rec, prec


def draw_rec_prec(rec, prec, mrec, mprec, class_name, ap):
    """
     Draw plot
    """
    plt.plot(rec, prec, '-o')
    # add a new penultimate point to the list (mrec[-2], 0.0)
    # since the last line segment (and respective area) do not affect the AP value
    area_under_curve_x = mrec[:-1] + [mrec[-2]] + [mrec[-1]]
    area_under_curve_y = mprec[:-1] + [0.0] + [mprec[-1]]
    plt.fill_between(area_under_curve_x, 0, area_under_curve_y, alpha=0.2, edgecolor='r')
    # set window title
    fig = plt.gcf() # gcf - get current figure
    fig.canvas.set_window_title('AP ' + class_name)
    # set plot title
    plt.title('class: ' + class_name + ' AP = {}%'.format(ap*100))
    #plt.suptitle('This is a somewhat long figure title', fontsize=16)
    # set axis titles
    plt.xlabel('Recall')
    plt.ylabel('Precision')
    # optional - set axes
    axes = plt.gca() # gca - get current axes
    axes.set_xlim([0.0,1.0])
    axes.set_ylim([0.0,1.05]) # .05 to give some extra space
    # Alternative option -> wait for button to be pressed
    #while not plt.waitforbuttonpress(): pass # wait for key display
    # Alternative option -> normal display
    #plt.show()
    # save the plot
    rec_prec_plot_path = os.path.join('result','classes')
    touchdir(rec_prec_plot_path)
    fig.savefig(os.path.join(rec_prec_plot_path, class_name + ".jpg"))
    plt.cla() # clear axes for next plot


def adjust_axes(r, t, fig, axes):
    """
     Plot - adjust axes
    """
    # get text width for re-scaling
    bb = t.get_window_extent(renderer=r)
    text_width_inches = bb.width / fig.dpi
    # get axis width in inches
    current_fig_width = fig.get_figwidth()
    new_fig_width = current_fig_width + text_width_inches
    propotion = new_fig_width / current_fig_width
    # get axis limit
    x_lim = axes.get_xlim()
    axes.set_xlim([x_lim[0], x_lim[1]*propotion])


def draw_plot_func(dictionary, n_classes, window_title, plot_title, x_label, output_path, to_show, plot_color, true_p_bar):
    """
     Draw plot using Matplotlib
    """
    # sort the dictionary by decreasing value, into a list of tuples
    sorted_dic_by_value = sorted(dictionary.items(), key=operator.itemgetter(1))
    # unpacking the list of tuples into two lists
    sorted_keys, sorted_values = zip(*sorted_dic_by_value)
    #
    if true_p_bar != "":
        """
         Special case to draw in (green=true predictions) & (red=false predictions)
        """
        fp_sorted = []
        tp_sorted = []
        for key in sorted_keys:
            fp_sorted.append(dictionary[key] - true_p_bar[key])
            tp_sorted.append(true_p_bar[key])
        plt.barh(range(n_classes), fp_sorted, align='center', color='crimson', label='False Predictions')
        plt.barh(range(n_classes), tp_sorted, align='center', color='forestgreen', label='True Predictions', left=fp_sorted)
        # add legend
        plt.legend(loc='lower right')
        """
         Write number on side of bar
        """
        fig = plt.gcf() # gcf - get current figure
        axes = plt.gca()
        r = fig.canvas.get_renderer()
        for i, val in enumerate(sorted_values):
            fp_val = fp_sorted[i]
            tp_val = tp_sorted[i]
            fp_str_val = " " + str(fp_val)
            tp_str_val = fp_str_val + " " + str(tp_val)
            # trick to paint multicolor with offset:
            #   first paint everything and then repaint the first number
            t = plt.text(val, i, tp_str_val, color='forestgreen', va='center', fontweight='bold')
            plt.text(val, i, fp_str_val, color='crimson', va='center', fontweight='bold')
            if i == (len(sorted_values)-1): # largest bar
                adjust_axes(r, t, fig, axes)
    else:
      plt.barh(range(n_classes), sorted_values, color=plot_color)
      """
       Write number on side of bar
      """
      fig = plt.gcf() # gcf - get current figure
      axes = plt.gca()
      r = fig.canvas.get_renderer()
      for i, val in enumerate(sorted_values):
          str_val = " " + str(val) # add a space before
          if val < 1.0:
              str_val = " {0:.2f}".format(val)
          t = plt.text(val, i, str_val, color=plot_color, va='center', fontweight='bold')
          # re-set axes to show number inside the figure
          if i == (len(sorted_values)-1): # largest bar
              adjust_axes(r, t, fig, axes)
    # set window title
    fig.canvas.set_window_title(window_title)
    # write classes in y axis
    tick_font_size = 12
    plt.yticks(range(n_classes), sorted_keys, fontsize=tick_font_size)
    """
     Re-scale height accordingly
    """
    init_height = fig.get_figheight()
    # comput the matrix height in points and inches
    dpi = fig.dpi
    height_pt = n_classes * (tick_font_size * 1.4) # 1.4 (some spacing)
    height_in = height_pt / dpi
    # compute the required figure height
    top_margin = 0.15    # in percentage of the figure height
    bottom_margin = 0.05 # in percentage of the figure height
    figure_height = height_in / (1 - top_margin - bottom_margin)
    # set new height
    if figure_height > init_height:
        fig.set_figheight(figure_height)

    # set plot title
    plt.title(plot_title, fontsize=14)
    # set axis titles
    # plt.xlabel('classes')
    plt.xlabel(x_label, fontsize='large')
    # adjust size of window
    fig.tight_layout()
    # save the plot
    fig.savefig(output_path)
    # show image
    if to_show:
        plt.show()
    # close the plot
    plt.close()


def calc_AP(gt_records, pred_records, class_name):
    '''
    Calculate AP value for one class records

    Param
         gt_records: ground truth records list for one class, with format:
                     [
                      ['image_file', 'xmin,ymin,xmax,ymax'],
                      ['image_file', 'xmin,ymin,xmax,ymax'],
                      ...
                     ]
         pred_record: predict records for one class, with format:
                     [
                      ['image_file', 'xmin,ymin,xmax,ymax', score],
                      ['image_file', 'xmin,ymin,xmax,ymax', score],
                      ...
                     ]
    Return
         AP value for the class
    '''
    # append usage flag in gt_records for matching gt search
    gt_records = [gt_record + ['unused'] for gt_record in gt_records]

    # init true_positive and false_positive list
    nd = len(pred_records)  # number of predict data
    true_positive = [0] * nd
    false_positive = [0] * nd
    true_positive_count = 0
    # assign predictions to ground truth objects
    for idx, pred_record in enumerate(pred_records):
        # filter out gt record from same image
        image_gt_records = [ gt_record for gt_record in gt_records if gt_record[0] == pred_record[0]]

        i = match_gt_box(pred_record, image_gt_records, iou_threshold=0.5)
        if i != -1:
            # find a valid gt obj to assign, set
            # true_positive list and mark image_gt_records.
            #
            # trick: gt_records will also be marked
            # as 'used', since image_gt_records is a
            # reference list
            image_gt_records[i][2] = 'used'
            true_positive[idx] = 1
            true_positive_count += 1
        else:
            false_positive[idx] = 1

    # compute precision/recall
    rec, prec = get_rec_prec(true_positive, false_positive, gt_records)
    ap, mrec, mprec = voc_ap(rec, prec)
    draw_rec_prec(rec, prec, mrec, mprec, class_name, ap)

    return ap, true_positive_count


def compute_mAP(opt, annotation_file, class_names, save_result):
    '''
    Compute mAP for YOLO model on annotation dataset
    '''
    annotation_records, gt_classes_records = annotation_parse(annotation_file, class_names)
    pred_classes_records = get_prediction_class_records(opt, annotation_records, class_names, save_result)

    APs = {}
    count_true_positives = {}
    #get AP value for each of the ground truth classes
    for _, class_name in enumerate(class_names):
        gt_records = gt_classes_records[class_name]
        #if we didn't detect any obj for a class, record 0
        if class_name not in pred_classes_records:
            APs[class_name] = 0.
            continue
        pred_records = pred_classes_records[class_name]
        ap, true_positive_count = calc_AP(gt_records, pred_records, class_name)
        APs[class_name] = ap
        count_true_positives[class_name] = true_positive_count

    #get mAP percentage value
    mAP = np.mean(list(APs.values()))*100

    '''
     Plot the total number of occurences of each class in the ground-truth
    '''
    gt_counter_per_class = {}
    for (class_name,info_list) in gt_classes_records.items():
        gt_counter_per_class[class_name] = len(info_list)

    window_title = "Ground-Truth Info"
    plot_title = "Ground-Truth\n" + "(" + str(len(annotation_records)) + " files and " + str(len(gt_classes_records)) + " classes)"
    x_label = "Number of objects per class"
    output_path = os.path.join('result','Ground-Truth Info.jpg')
    draw_plot_func(gt_counter_per_class, len(gt_classes_records), window_title, plot_title, x_label, output_path, to_show=False, plot_color='forestgreen', true_p_bar='')


    '''
     Plot the total number of occurences of each class in the "predicted" folder
    '''
    pred_counter_per_class = {}
    for (class_name,info_list) in pred_classes_records.items():
        pred_counter_per_class[class_name] = len(info_list)

    window_title = "Predicted Objects Info"
    # Plot title
    plot_title = "Predicted Objects\n" + "(" + str(len(annotation_records)) + " files and "
    count_non_zero_values_in_dictionary = sum(int(x) > 0 for x in list(pred_counter_per_class.values()))
    plot_title += str(count_non_zero_values_in_dictionary) + " detected classes)"
    # end Plot title
    x_label = "Number of objects per class"
    output_path = os.path.join('result','Predicted Objects Info.jpg')
    draw_plot_func(pred_counter_per_class, len(pred_counter_per_class), window_title, plot_title, x_label, output_path, to_show=False, plot_color='forestgreen', true_p_bar=count_true_positives)


    '''
     Draw mAP plot (Show AP's of all classes in decreasing order)
    '''
    window_title = "mAP"
    plot_title = "mAP = {0:.2f}%".format(mAP)
    x_label = "Average Precision"
    output_path = os.path.join('result','mAP.jpg')
    draw_plot_func(APs, len(gt_classes_records), window_title, plot_title, x_label, output_path, to_show=True, plot_color='royalblue', true_p_bar='')

    #get mAP from APs
    for (class_name, AP) in APs.items():
        print('AP for {}: {}'.format(class_name, AP))

    #return mAP percentage value
    return mAP



def main():
    opt = opts()
    parser = opt.parser

    '''
    Command line options
    '''

    parser.add_argument(
        '--classes_path', type=str,
        help='path to class definitions, default model_data/voc_classes.txt', default='model_data/voc_classes.txt')

    #parser.add_argument(
        #'--model_image_size', type=str,
        #help='model image input size as <num>x<num>, default 512x512', default='512x512')

    parser.add_argument(
        '--annotation_file', type=str,
        help='annotation txt file to varify')

    parser.add_argument(
        '--save_result', default=False, action="store_true",
        help='Save the detection result image in detection/ dir'
    )

    opt = opt.parse()

    #if not args.annotation_file:
        #raise ValueError('annotation file is not specified')

    # param parse
    class_names = get_classes(opt.classes_path)
    #height, width = args.model_image_size.split('x')
    #model_image_size = (int(height), int(width))

    mAP = compute_mAP(opt, opt.annotation_file, class_names, opt.save_result)
    print('mAP result: {}'.format(mAP))


if __name__ == '__main__':
    main()
