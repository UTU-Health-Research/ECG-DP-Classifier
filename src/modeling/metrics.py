from sklearn.metrics import roc_auc_score, roc_curve, auc, average_precision_score
import numpy as np
import torch
import pandas as pd
import matplotlib.pyplot as plt
import os, sys
from sklearn.metrics import classification_report

def cal_classification_report(y_true, y_pre, labels, threshold=0.5):

    true_labels, pre_prob, _, cls_labels = preprocess_labels(y_true, y_pre, labels, threshold)
    
    pre_prob = (pre_prob > threshold).astype(int)

    return classification_report(true_labels, pre_prob, target_names=cls_labels)

def cal_multilabel_metrics(y_true, y_pre, labels, threshold=0.5):
    ''' Compute micro/macro AUROC and AUPRC
    
    :param y_true: Actual class labels
    :type y_true: torch.Tensor
    :param y_pre: Logits of predictions
    :type y_pre: torch.Tensor
    :param labels: Class labels used in the classification as SNOMED CT Codes
    :type labels: list
    
    :return: wanted metrics
    :rtypes: float
    '''

    # Convert tensors to numpy and filter out empty classes
    true_labels, pre_prob, _, cls_labels = preprocess_labels(y_true, y_pre, labels, threshold)

    # ---------------- Wanted metrics ----------------

    # -- Average precision score
    macro_avg_prec = average_precision_score(true_labels, pre_prob, average = 'macro')
    micro_avg_prec = average_precision_score(true_labels, pre_prob, average = 'micro')
    

    # -- AUROC score
    micro_auroc = roc_auc_score(true_labels, pre_prob, average = 'micro')
    macro_auroc = roc_auc_score(true_labels, pre_prob, average = 'macro')
    
    # -- PhysioNet Challenge 2021 score
    
    # Need all the labels
    true_labels, _, binary_outputs, cls_labels = preprocess_labels(y_true, y_pre, labels, threshold, drop_missing=False)
    challenge_metric = physionet_challenge_score(true_labels, binary_outputs, cls_labels)

    return macro_avg_prec, micro_avg_prec, macro_auroc, micro_auroc, challenge_metric

    
def preprocess_labels(y_true, y_pre, labels, threshold = 0.5, drop_missing = True):
    ''' Convert tensor variables to numpy and check the positive class labels. 
    If there's none, leave the columns out from actual labels, binary predictions,
    logits and class labels used in the classification.
    
    :param y_true: Actual class labels
    :type y_true: torch.Tensor
    :param y_pre: Logits of predicted labels
    :type y_pre: torch.Tensor
    
    :return true_labels, pre_prob, pre_binary, labels: Converted (and possibly filtered) actual labels,
                                                       binary predictions and logits

    :rtype: numpy.ndarrays
    '''

    # Actual labels from tensor to numpy
    true_labels = y_true.cpu().detach().numpy().astype(np.int32)  

    # Logits from tensor to numpy
    pre_prob = y_pre.cpu().detach().numpy().astype(np.float32)
    
    # ------ One-hot-endcode predicted labels ------

    pre_binary = np.zeros(pre_prob.shape, dtype=np.int32)

    # Find the index of the maximum value within the logits
    likeliest_dx = np.argmax(pre_prob, axis=1)

    # First, add the most likeliest diagnosis to the predicted label
    #pre_binary[np.arange(true_labels.shape[0]), likeliest_dx] = 1

    # Then, add all the others that are above the decision threshold
    other_dx = pre_prob >= threshold

    pre_binary = pre_binary + other_dx
    pre_binary[pre_binary > 1.1] = 1
    pre_binary = np.squeeze(pre_binary) 

    if drop_missing:
        
         # ------ Check the positive class labels ------
    
        # Find all the columnwise indexes where there's no positive class
        null_idx = np.argwhere(np.all(true_labels[..., :] == 0, axis=0))

        # Drop the all-zero columns from actual labels, logits,
        # binary predictions and class labels used in the classification
        if any(null_idx):
            true_labels = np.delete(true_labels, null_idx, axis=1)
            pre_prob = np.delete(pre_prob, null_idx, axis=1)
            pre_binary = np.delete(pre_binary, null_idx, axis=1)
            labels = np.delete(labels, null_idx)

    # There should be as many actual labels and logits as there are labels left
    assert true_labels.shape[1] == pre_prob.shape[1] == pre_binary.shape[1] == len(labels)
    
    return true_labels, pre_prob, pre_binary, labels


def physionet_challenge_score(y_true, y_pre, labels):
    ''' Compute the PhysioNet Challenge 2021 metric based on the actual and
    predicted labels. The scoring awards full credit to correct diagnoses and
    partial credit to misdiagnoses that result in similar treatments or
    outcomes as the true diagnosis as judged by the cardiologists.
        
    :param y_true: Actual class labels
    :type y_true: numpy.ndarray
    :param y_pre: One-hot-encoded predicted labels
    :type y_pre: numpy.ndarray
    :labels: Class labels used in the classification as SNOMED CT Codes
    :type labels: list
    
    :return: challenge metric
    :rtype: float
    '''    
    

   # -------- Load the Physionet Challenge scored classes --------

    equivalent_classes = ['59118001', '63593006', '17338001', '164909002']
    labels_file = os.path.join('/home/zoorab/12-lead-ecg-classifier', 'data', 'scored_diagnoses_2021.csv')
    label_df = pd.read_csv(labels_file)

    # Remove equivalent classes (the ones from PhysioNet Challenge 2021)
    classes = sorted(list(set([str(name) for name in label_df['SNOMEDCTCode']]) - set(equivalent_classes)))
    num_classes = len(classes)

    # -------- Load the Physionet Challenge weights --------
    
    # Load the csv file of the weights
    weights_file = os.path.join('/home/zoorab/12-lead-ecg-classifier', 'data', 'physionet2021_weights.csv')
    weights_df = pd.read_csv(weights_file, index_col=0)
    indeces = list(np.ravel(weights_df.index))
    columns = list(np.ravel(weights_df.columns))

    assert indeces == columns, 'Columns and indexes in the weight file don´t match'
    assert len(indeces) > 1, 'The weight dataframe is empty!'
    assert len(columns) > 1, 'The weight dataframe is empty!'

    # Assign the entries of the weight matrix with indeces and columns corresponding to the classes
    weights = np.zeros((num_classes, num_classes), dtype=np.float64)
    for i, a in enumerate(indeces):
        if a in classes:
            k = classes.index(a)
            for j, b in enumerate(indeces):
                if b in classes:
                    l = classes.index(b)
                    weights[k, l] = weights_df.values[i, j]

    # ------------- Reshape actual and predicted labels -------------

    # We need the arrays of actual and perdicted labels to be in the shape of 
    # <num of recording> X <num of scored labels in Physionet Challenge 2021>

    true_labels = np.zeros((len(y_true), num_classes), dtype=np.bool_)
    binary_outputs = np.zeros((len(y_pre), num_classes), dtype=np.bool_)
    
    # Iterate over classification labels and if one has been scored, store 
    # the corresponding actual label and logit
    for i, l in enumerate(labels):
        if l in classes:
            class_index = classes.index(l)
            true_labels[:, class_index] = y_true[:, i]
            binary_outputs[:, class_index] = y_pre[:, i]

    # ------------- Challenge metric -------------

    sinus_rhythm = '426783006'
    challenge_metric = compute_challenge_metric(weights,
                                                true_labels,
                                                binary_outputs,
                                                classes,
                                                sinus_rhythm)

    return challenge_metric


def compute_modified_confusion_matrix(labels, outputs):
    '''Compute a modified confusion matrix for multi-class, multi-label tasks.
    
    :param labels: Actual class labels
    :type labels: numpy.ndarray
    :param outputs: One-hot-encoded predicted class labels
    :type outputs: numpy.ndarray
    
    :return A: Multi-class, multi-label confusion matrix
    :rtype: numpy.ndarray
    
    '''

    # Compute a binary multi-class, multi-label confusion matrix, where the rows
    # are the labels and the columns are the outputs.
    num_recordings, num_classes = np.shape(labels)
    A = np.zeros((num_classes, num_classes))

    # Iterate over all of the recordings.
    for i in range(num_recordings):
        # Calculate the number of positive labels and/or outputs.
        normalization = float(max(np.sum(np.any((labels[i, :], outputs[i, :]), axis=0)), 1))
        # Iterate over all of the classes.
        for j in range(num_classes):
            # Assign full and/or partial credit for each positive class.
            if labels[i, j]:
                for k in range(num_classes):
                    if outputs[i, k]:
                        A[j, k] += 1.0/normalization
    return A


def compute_challenge_metric(weights, labels, outputs, classes, sinus_rhythm):
    ''' Compute the evaluation metric for the Challenge.
    
    :param weights: Physionet Challenge weight for each label
    :type weights: numpy.ndarray
    :param labels: Actual class labels
    :type labels: numpy.ndarray
    :param outputs: One-hot-encoded predicted labels
    :type outputs: numpy.ndarray
    :param classes: Labels used in scoring as SNOMED CT Codes
    :type classes: list
    :param sinus_rhythm: SNOMED CT Code of sinus rhythm
    :type sinus_rhythm: str
    
    :return normalized_score: normalized challenge metric
    :rtype: float
    '''
    
    num_recordings, num_classes = np.shape(labels)
    if sinus_rhythm in classes:
        sinus_rhythm_index = classes.index(sinus_rhythm)
    else:
        raise ValueError('The sinus rhythm class is not available.')

    # Compute the observed score.
    A = compute_modified_confusion_matrix(labels, outputs)
    observed_score = np.nansum(weights * A)

    # Compute the score for the model that always chooses the correct label(s).
    correct_outputs = labels
    A = compute_modified_confusion_matrix(labels, correct_outputs)
    correct_score = np.nansum(weights * A)

    # Compute the score for the model that always chooses the sinus rhythm class.
    inactive_outputs = np.zeros((num_recordings, num_classes), dtype=np.bool_)
    inactive_outputs[:, sinus_rhythm_index] = 1
    A = compute_modified_confusion_matrix(labels, inactive_outputs)
    inactive_score = np.nansum(weights * A)

    if correct_score != inactive_score:
        normalized_score = float(observed_score - inactive_score) / float(correct_score - inactive_score)
    else:
        normalized_score = 0.0

    return normalized_score


def roc_curves(y_true, y_pre, labels, epoch=None, save_path='./experiments/',add_DP_path=''):
    '''Compute and plot the ROC Curves for each class, also macro and micro. Save as a png image.
    
    :param y_true: Actual labels
    :type y_true: torch.Tensor
    :param y_pred: Logits of predicted labels
    :type y_pred: torch.Tensor
    :param labels: Class labels used in the classification as SNOMED CT Codes
    :type labels: list
    :param epoch: Epoch in which the predictions are made
    :type epoch: int
    '''

    # Convert tensors to numpy and filter out empty classes
    true_labels, pre_prob, _, cls_labels = preprocess_labels(y_true, y_pre, labels, drop_missing=True)
    
    fpr, tpr, roc_auc = dict(), dict(), {'labels':{}}
    # AUROC, fpr and tpr for each label
    for i in range(len(cls_labels)):
        fpr[i], tpr[i], _ = roc_curve(true_labels[:, i], pre_prob[:, i])
        roc_auc['labels'][cls_labels[i]] = auc(fpr[i], tpr[i])
    
    # Compute micro-average ROC curve and ROC area
    fpr["micro"], tpr["micro"], _ = roc_curve(true_labels.ravel(), pre_prob.ravel())
    roc_auc["micro"] = auc(fpr["micro"], tpr["micro"])

    # Interpolate all ROC curves at these points to compute macro-average ROC area
    fpr_grid = np.linspace(0.0, 1.0, 1000)
    mean_tpr = np.zeros_like(fpr_grid)
    for i in range(len(cls_labels)):
        mean_tpr += np.interp(fpr_grid, fpr[i], tpr[i])  # linear interpolation

    # Average the mean TPR and compute AUC
    mean_tpr /= len(cls_labels)
    
    fpr["macro"] = fpr_grid
    tpr["macro"] = mean_tpr

    roc_auc["fpr_macro"],roc_auc['tpr_macro'],roc_auc['fpr_micro'],roc_auc['tpr_micro'] =\
    fpr["macro"],tpr["macro"],fpr["micro"], tpr["micro"]

    roc_auc["macro"] = auc(fpr["macro"], tpr["macro"])
 
    return roc_auc


if __name__ == '__main__':

    y_actual = torch.Tensor([[1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
        [1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
        [1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
        [1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
        [1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
        [1., 0., 1., 0., 0., 0., 0., 0., 0., 0., 0., 1., 0., 0., 0., 0., 0.],
        [1., 0., 0., 0., 1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
        [1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.]])

    y_prob = torch.Tensor([[0.0000e+00, 1.0000e+00, 1.0000e+00, 0.0000e+00, 0.0000e+00, 1.0000e+00,
         1.0000e+00, 0.0000e+00, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00,
         1.0000e+00, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00],
        [7.5481e-34, 1.0000e+00, 1.0000e+00, 3.5030e-19, 7.4219e-26, 1.0000e+00,
         1.0000e+00, 1.4667e-36, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00,
         1.0000e+00, 1.0000e+00, 2.0872e-25, 1.0000e+00, 1.0000e+00],
        [4.9990e-28, 1.0000e+00, 1.0000e+00, 8.5356e-28, 3.5239e-24, 1.0000e+00,
         1.0000e+00, 0.0000e+00, 1.0000e+00, 0.0000e+00, 1.0000e+00, 8.3170e-01,
         1.0000e+00, 1.0000e+00, 1.2724e-32, 1.0000e+00, 1.0000e+00],
        [1.8659e-09, 8.7257e-06, 1.0000e+00, 3.9260e-33, 8.4741e-31, 1.0000e+00,
         1.0000e+00, 9.1425e-25, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00,
         1.0000e+00, 6.5847e-24, 0.0000e+00, 1.0000e+00, 1.0000e+00],
        [1.3829e-29, 1.0000e+00, 1.0000e+00, 6.8302e-31, 6.7060e-20, 1.0000e+00,
         1.0000e+00, 0.0000e+00, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00,
         1.0000e+00, 1.0000e+00, 6.5582e-24, 1.0000e+00, 1.0000e+00],
        [3.7383e-10, 2.8861e-02, 1.0000e+00, 1.6027e-24, 1.4673e-14, 1.0000e+00,
         1.0000e+00, 4.0820e-28, 1.0000e+00, 0.0000e+00, 1.0000e+00, 4.6228e-02,
         1.0000e+00, 9.0010e-05, 8.0360e-18, 1.0000e+00, 1.0000e+00],
        [1.1432e-22, 9.9910e-01, 1.0000e+00, 1.1678e-17, 4.7706e-29, 1.0000e+00,
         1.0000e+00, 0.0000e+00, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00,
         1.0000e+00, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00],
        [2.4080e-23, 1.0000e+00, 1.0000e+00, 8.2847e-15, 3.4797e-17, 1.0000e+00,
         1.0000e+00, 4.6156e-36, 1.0000e+00, 0.0000e+00, 1.0000e+00, 1.0000e+00,
         1.0000e+00, 1.0000e+00, 8.2097e-26, 1.0000e+00, 1.0000e+00]])
    
    labels = ['426783006', '426177001', '164934002', '427393009', '713426002',
       '427084000', '59118001', '164889003', '59931005', '47665007',
       '445118002', '39732003', '164890007', '164909002', '270492004',
       '251146004', '284470004']

    cal_multilabel_metrics(y_actual, y_prob, labels, threshold=0.5)