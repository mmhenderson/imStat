import os
import numpy as np
import pandas as pd
import argparse
from scipy.io import loadmat
import copy

from analyze_features import bent_gabor_bank
from utils import default_paths


def process_cluster_ims(debug=False):
    
    """
    For each of the sketch tokens cluster images, quantify the approximate orientation and 
    curvature of the feature. Save result as a csv file.
    """
    
    if not debug:
        try:
            device = initialize_fitting.init_cuda()
        except:
            device = 'cpu:0'
        print('Found device:')
        print(device)   
    else:
        device = 'cpu:0'

    # load the images
    cluster_fn = os.path.join('/user_data/mmhender/toolboxes/SketchTokens/', 'clusters.mat')
    clust = loadmat(cluster_fn)
    feature_ims = clust['clusters']['clusters'][0][0]
    n_clusters = feature_ims.shape[2]
    
    # define a set of features to test 
    bends = np.linspace(0, 0.30, 8)
    alphaA = np.linspace(0,2*np.pi,73)[0:72]
    freq_values_cyc_per_image = np.linspace(2,7,4)

    bend_values = bends
    orient_values = alphaA
    
    if debug:
        bend_values = bend_values[0:2]
        orient_values = orient_values[0:2]
   
    cropped_size = 30;
    bank = bent_gabor_bank.bent_gabor_feature_bank(freq_values = freq_values_cyc_per_image, \
                                           bend_values = bend_values, \
                                           orient_values = orient_values, \
                                           image_size=cropped_size, \
                                           device = device)
    # need an even number of pixels
    feature_ims_trimmed = feature_ims[0:cropped_size,0:cropped_size,:]
    # filter with straight gabor filters
    all_lin_filt_coeff = bank.filter_image_batch_pytorch(feature_ims_trimmed, which_kernels='linear')
    # filter with bent "banana" gabors
    all_curv_filt_coeff = bank.filter_image_batch_pytorch(feature_ims_trimmed, which_kernels='curv')
    
    # find the kernel that gives the max activation in the image center
    # (the cluster images are centered, so expect the best match to be close to center)
    center = int(cropped_size/2)
    npix_out = 3;
    center_bbox = [center-npix_out, center+npix_out, center-npix_out, center+npix_out]
    
    lin_center = all_lin_filt_coeff[center_bbox[0]:center_bbox[1], \
                                    center_bbox[2]:center_bbox[3]]
    curv_center = all_curv_filt_coeff[center_bbox[0]:center_bbox[1], \
                                    center_bbox[2]:center_bbox[3]]
    
    max_lin_center = np.max(np.max(lin_center, axis=0), axis=0)
    max_curv_center = np.max(np.max(curv_center, axis=0), axis=0)
    
    # for the linear filters, save the full profile of activation over orientation
    max_lin_whole_image = np.max(np.max(all_lin_filt_coeff, axis=0), axis=0)
    features_use = bank.lin_kernel_pars[:,0]==np.min(bank.lin_kernel_pars[:,0])
    max_activ_each_orient = max_lin_whole_image[features_use,:].T
    
    
    # identify which feature led to the best activ, and what the activation value was
    best_lin_kernel = np.argmax(max_lin_center, axis=0)
    max_lin_activ = np.max(max_lin_center, axis=0)
   
    best_curv_kernel = np.argmax(max_curv_center, axis=0)
    max_curv_activ = np.max(max_curv_center, axis=0)
   
    # was the overall best kernel a curved or linear gabor?
    which_best = np.argmax(np.array([max_lin_activ, max_curv_activ]), axis=0)

    # make a table to save things
    best_scale = bank.lin_kernel_pars[best_lin_kernel,0]
    best_bend = bank.lin_kernel_pars[best_lin_kernel,1]
    best_orient = bank.lin_kernel_pars[best_lin_kernel,2]
    
    best_scale[which_best==1] = \
        bank.curv_kernel_pars[best_curv_kernel[which_best==1],0]
    best_bend[which_best==1] = \
        bank.curv_kernel_pars[best_curv_kernel[which_best==1],1]
    best_orient[which_best==1] = \
        bank.curv_kernel_pars[best_curv_kernel[which_best==1],2]
     
    best_freq = best_scale * cropped_size / (2*np.pi)
    
    grouping_df = get_simple_groupings()
    
    quant_df = pd.DataFrame({'best_freq': best_freq, 'best_scale': best_scale, \
                             'best_bend': best_bend, 'best_orient': best_orient, \
                             'best_linear_index': best_lin_kernel, 'best_curv_index': best_curv_kernel, \
                             'which_best': which_best})
    
    final_df = quant_df.join(grouping_df)

    fn2save = os.path.join(default_paths.sketch_token_feat_path,\
                           'Sketch_tokens_info_table.csv')
    print('saving to %s'%fn2save)
    final_df.to_csv(fn2save,index=False)
    
    fn2save = os.path.join(default_paths.sketch_token_feat_path,\
                           'Sketch_tokens_orient_profiles.npy')
    print('saving to %s'%fn2save)
    np.save(fn2save, max_activ_each_orient)


def get_simple_groupings():
    
    # qualitative groupings of the sketch tokens features, based on whether they are 
    # most like two separate lines (~parallel), a junction of two lines (T or L) or 
    # a single line. Single lines can be curved, the bent gabor filters will measure curvature.
    double_lines = [17, 27, 30, 32, 33, 34, 35, 40, 51, 59, 60, 61, 64, 65, 66, 68, 69, 73, 75, 78, 80, 84, \
                      88, 90, 96, 106, 108, 110, 111, 113, 114, 132, 133, 134, 136]
    is_double = np.isin(np.arange(150), double_lines)
    junction = [38, 62, 63, 67, 70, 71, 77, 79, 81, 86, 94, 100, 102, 103, 112, 115, 116, 118, 119, 120, 124, 125, 126, \
               137, 138, 140, 142, 143, 144, 145, 146, 149]
    is_junction = np.isin(np.arange(150), junction)
    is_single = ~is_double &  ~is_junction

    grouping_df = pd.DataFrame({'double': is_double, 'junction': is_junction, 'single': is_single})
    
    return grouping_df

    
if __name__ == '__main__':
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--debug", type=int,default=0,
                    help="want to run a fast test version of this script to debug? 1 for yes, 0 for no")
    
    args = parser.parse_args()
    
    process_cluster_ims(debug=args.debug==True)