import sys
import os
import struct
import time
import copy
import numpy as np
import h5py
from tqdm import tqdm
import pickle
import math
import sklearn
from sklearn import decomposition

import torch
import torch.nn as nn
import torch.nn.init as I
import torch.nn.functional as F
import torch.optim as optim

from utils import numpy_utils, torch_utils, texture_utils

"""
General code for fitting a 'feature weighted receptive field' model to fmri data - looping over many candidate pRF 
models for each voxel, find a set of weights that best predict its responses based on feature space of interest.
Can work for many different types of feature spaces, feature extraction implemented with nn.Module.

Original source of some of this code is the github repository:
https://github.com/styvesg/nsd
It was modified by MH to work for this project.
"""


def _cofactor_fn_cpu(_x, lambdas):
    '''
    Generating a matrix needed to solve ridge regression model for each lambda value.
    Ridge regression (Tikhonov) solution is :
    w = (X^T*X + I*lambda)^-1 * X^T * Y
    This func will return (X^T*X + I*lambda)^-1 * X^T. 
    So once we have that, can just multiply by training data (Y) to get weights.
    returned size is [nLambdas x nFeatures x nTrials]
    This version makes sure that the torch inverse operation is done on the cpu, and in floating point-64 precision.
    Otherwise get bad results for small lambda values. This seems to be a torch-specific bug, noted around May 2021.
    
    '''
    device_orig = _x.device
    type_orig = _x.dtype
    # switch to this specific format which works with inverse
    _x = _x.to('cpu').to(torch.float64)
    _f = torch.stack([(torch.mm(torch.t(_x), _x) + torch.eye(_x.size()[1], device='cpu', dtype=torch.float64) * l).inverse() for l in lambdas], axis=0) 
    
    # [#lambdas, #feature, #feature] 
    cof = torch.tensordot(_f, _x, dims=[[2],[1]]) # [#lambdas, #feature, #sample]
    
    # put back to whatever way it was before, so that we can continue with other operations as usual
    return cof.to(device_orig).to(type_orig)



def _loss_fn(_cofactor, _vtrn, _xout, _vout):
    '''
    Calculate loss given "cofactor" from cofactor_fn, training data, held-out design matrix, held out data.
    returns weights (betas) based on equation
    w = (X^T*X + I*lambda)^-1 * X^T * Y
    also returns loss for these weights w the held out data. SSE is loss func here.
    '''

    _beta = torch.tensordot(_cofactor, _vtrn, dims=[[2], [0]]) # [#lambdas, #feature, #voxel]
    _pred = torch.tensordot(_xout, _beta, dims=[[1],[1]]) # [#samples, #lambdas, #voxels]
    _loss = torch.sum(torch.pow(_vout[:,None,:] - _pred, 2), dim=0) # [#lambdas, #voxels]
    return _beta, _loss


def fit_fwrf_model(images, voxel_data, _feature_extractor, prf_models, lambdas, \
                   zscore=False, add_bias=False, voxel_batch_size=100, holdout_size=100, \
                       shuffle=True, shuff_rnd_seed=0, device=None, debug=False):
    
    """
    Solve for encoding model weights using ridge regression.
    Inputs:
        images: the training images, [n_trials x 1 x height x width]
        voxel_data: the training voxel data, [n_trials x n_voxels]
        _feature_extractor_fn: module that maps from images to model features
        prf_models: the list of possible pRFs to test, columns are [x, y, sigma]
        lambdas: ridge lambda parameters to test
        zscore: want to zscore each column of feature matrix before fitting?
        add_bias: add a column of ones to feature matrix, for an additive bias?
        voxel_batch_size: how many voxels to use at a time for model fitting
        holdout_size: how many training trials to hold out for computing loss/lambda selection?
        shuffle: do we shuffle training data order before holding trials out?      
        shuff_rnd_seed: if we do shuffle training data (shuffle=True), what random seed to use? if zero, choose a new random seed in this code.
        device: what device to use? cpu/cuda
        debug: want to run a shortened version of this, to test it?
    Outputs:
        best_losses: loss value for each voxel (with best pRF and best lambda), eval on held out set
        best_lambdas: best lambda for each voxel (chosen based on loss w held out set)
        best_params: 
            [0] best pRF for each voxel [x,y,sigma]
            [1] best weights for each voxel/feature
            [2] if add_bias=True, best bias value for each voxel
            [3] if zscore=True, the mean of each feature before z-score
            [4] if zscore=True, the std of each feature before z-score
            [5] index of the best pRF for each voxel (i.e. index of row in "prf_models")
        
    """

    dtype = images.dtype.type
    if device is None:
        device=torch.device('cpu:0')

    print ('dtype = %s' % dtype)
    print ('device = %s' % device)

    n_trials = len(images)
    n_prfs = len(prf_models)
    n_voxels = voxel_data.shape[1]   

    # Get train/holdout splits.
    # Held-out data here is used for lamdba selection.
    # This is the inner part of nested cross-validation; there is another portion of data ('val') which never enters this function.
    trn_size = n_trials - holdout_size
    assert trn_size>0, 'Training size needs to be greater than zero'
    print ('trn_size = %d (%.1f%%)' % (trn_size, float(trn_size)*100/len(voxel_data)))
    order = np.arange(len(voxel_data), dtype=int)
    if shuffle:
        if shuff_rnd_seed==0:
            print('Computing a new random seed')
            shuff_rnd_seed = int(time.strftime('%M%H%d', time.localtime()))
        print('Seeding random number generator: seed is %d'%shuff_rnd_seed)
        np.random.seed(shuff_rnd_seed)
        np.random.shuffle(order)
    images = images[order]
    voxel_data = voxel_data[order]  
    trn_data = voxel_data[:trn_size]
    out_data = voxel_data[trn_size:]

    
    # Here is where any model-specific additional initialization steps are done
    # Includes initializing pca params arrays, if doing pca
    _feature_extractor.init_for_fitting(images.shape[2:4], prf_models, dtype)
    max_features = _feature_extractor.max_features

    # Decide whether to do any "partial" versions of the models (leaving out subsets of features)
    # Purpose is for variance partition
    masks, partial_version_names = _feature_extractor.get_partial_versions()
    n_partial_versions = len(partial_version_names) # will be one if skipping varpart
    if add_bias:
        masks = np.concatenate([masks, np.ones([masks.shape[0],1])], axis=1) # always include intercept 
    masks = np.transpose(masks)
    # masks is [n_features_total (including intercept) x n_partial_versions]

    # Initialize arrays to store model fitting params
    best_w_params = np.zeros(shape=(n_voxels, max_features ,n_partial_versions), dtype=dtype)
    best_prf_models = np.full(shape=(n_voxels,n_partial_versions), fill_value=-1, dtype=int)   
    best_lambdas = np.full(shape=(n_voxels,n_partial_versions), fill_value=-1, dtype=int)
    best_losses = np.full(fill_value=np.inf, shape=(n_voxels,n_partial_versions), dtype=dtype)

    # Additional params that are optional
    if add_bias:
        best_w_params = np.concatenate([best_w_params, np.ones(shape=(n_voxels,1,n_partial_versions), dtype=dtype)], axis=1)

    if zscore:
        features_mean = np.zeros(shape=(n_voxels, max_features), dtype=dtype)
        features_std  = np.zeros(shape=(n_voxels, max_features), dtype=dtype)
    else:
        features_mean = None
        features_std = None

    start_time = time.time()
    vox_loop_time = 0

    print ('---------------------------------------\n')
    
    with torch.no_grad():
        
        # Looping over prf_models (here prf_models are different spatial RF definitions)
        for m,(x,y,sigma) in enumerate(prf_models):
            if debug and m>1:
                break
                
            print('\nGetting features for prf %d: [x,y,sigma] is [%.2f %.2f %.4f]'%(m, prf_models[m,0],  prf_models[m,1],  prf_models[m,2]))

            t = time.time()            

            # Get features for the desired pRF, across all trn set image            
            features, feature_inds_defined = _feature_extractor(images, (x,y,sigma), m, fitting_mode=True)
            features = features.detach().cpu().numpy() 
            
            elapsed = time.time() - t

            n_features_actual = features.shape[1]
            
            if zscore:  
                features_m = np.mean(features, axis=0, keepdims=True) #[:trn_size]
                features_s = np.std(features, axis=0, keepdims=True) + 1e-6          
                features -= features_m
                features /= features_s    

            if add_bias:
                features = np.concatenate([features, np.ones(shape=(len(features), 1), dtype=dtype)], axis=1)
                feature_inds_defined = np.concatenate((feature_inds_defined, [True]), axis=0)
                
            trn_features = features[:trn_size,:]
            out_features = features[trn_size:,:]
            
            
            # Going to keep track of whether current prf is better than running best, for each voxel.
            # This is for the full model only.
            # Will use this to make sure for each partial model, we end up saving the params for the prf that was best w full model.
            full_model_improved = np.zeros((n_voxels,),dtype=bool)

            # Looping over versions of model w different features set to zero (variance partition)
            for pp in range(n_partial_versions):

                print('\nFitting version %d of %d: %s, '%(pp, n_partial_versions, partial_version_names[pp]))

                nonzero_inds_full = np.logical_and(masks[:,pp], feature_inds_defined)             
               
                # Send matrices to gpu
                nonzero_inds_short = masks[feature_inds_defined,pp]==1
                _xtrn = torch_utils._to_torch(trn_features[:, nonzero_inds_short], device=device)
                _xout = torch_utils._to_torch(out_features[:, nonzero_inds_short], device=device)   

                # Do part of the matrix math involved in ridge regression optimization out of the loop, 
                # because this part will be same for all the voxels.
                _cof = _cofactor_fn_cpu(_xtrn, lambdas = lambdas) 

                # Now looping over batches of voxels (only reason is because can't store all in memory at same time)
                vox_start = time.time()
                vi=-1
                for rv,lv in numpy_utils.iterate_range(0, n_voxels, voxel_batch_size):
                    vi=vi+1
                    sys.stdout.write('\rfitting model %4d of %-4d, voxels [%6d:%-6d] of %d' % (m, n_prfs, rv[0], rv[-1], n_voxels))

                    # Send matrices to gpu
                    _vtrn = torch_utils._to_torch(trn_data[:,rv], device=device)
                    _vout = torch_utils._to_torch(out_data[:,rv], device=device)

                    # Here is where optimization happens - relatively simple matrix math inside loss fn.
                    _betas, _loss = _loss_fn(_cof, _vtrn, _xout, _vout) #   [#lambda, #feature, #voxel, ], [#lambda, #voxel]
                    # Now have a set of weights (in betas) and a loss value for every voxel and every lambda. 
                    # goal is then to choose for each voxel, what is the best lambda and what weights went with that lambda.

                    # choose best lambda value and the loss that went with it.
                    _loss_values, _lambda_index = torch.min(_loss, dim=0)
                    loss_values, lambda_index = torch_utils.get_value(_loss_values), torch_utils.get_value(_lambda_index)
                    betas = torch_utils.get_value(_betas)


                    if pp==0:

                        # comparing this loss to the other prf_models for each voxel (e.g. the other RF position/sizes)
                        assert(partial_version_names[pp]=='full_model' or partial_version_names[pp]=='full_combined_model')               
                        imp = loss_values<best_losses[rv,pp]
                        full_model_improved[rv] = imp

                    else:

                        # for the partial models we don't actually care which was best for the partial model itself,
                        # just care what was best for the full model
                        imp = full_model_improved[rv]


                    if np.sum(imp)>0:

                        # for whichever voxels had improvement relative to previous prf_models, save parameters now
                        # this means we won't have to save all params for all prf_models, just best.
                        arv = np.array(rv)[imp]

                        lambda_inds = lambda_index[imp]
                        best_lambdas[arv,pp] = lambda_inds
                        best_losses[arv,pp] = loss_values[imp]
                        best_prf_models[arv,pp] = m
                        if zscore:
                            
                            fmean_tmp = copy.deepcopy(features_mean[arv,:])
                            fstd_tmp = copy.deepcopy(features_std[arv,:])
                            fmean_tmp[:,nonzero_inds_full[0:-1]] = features_m[0,nonzero_inds_short[0:-1]] # broadcast over updated voxels
                            fmean_tmp[:,~nonzero_inds_full[0:-1]] = 0.0
                            fstd_tmp[:,nonzero_inds_full[0:-1]] = features_s[0,nonzero_inds_short[0:-1]] # broadcast over updated voxels
                            fstd_tmp[:,~nonzero_inds_full[0:-1]] = 0.0
                            features_mean[arv,:] = fmean_tmp
                            features_std[arv,:] = fstd_tmp
                            
                        # taking the weights associated with the best lambda value
                        # remember that they won't fill entire matrix, rest of values stay at zero
                        best_w_tmp = copy.deepcopy(best_w_params[arv,:,pp])
                        best_w_tmp[:,nonzero_inds_full] = numpy_utils.select_along_axis(betas[:,:,imp], lambda_inds, run_axis=2, choice_axis=0).T
                        best_w_tmp[:,~nonzero_inds_full] = 0.0 # make sure to fill zeros here

#                         # bias is always last value, even if zeros for some other features
#                         if add_bias:
#                             best_w_tmp[:,-1] = numpy_utils.select_along_axis(betas[:,-1,imp], lambda_inds, run_axis=1, choice_axis=0).T

                        best_w_params[arv,:,pp] = best_w_tmp
                
                vox_loop_time += (time.time() - vox_start)
                elapsed = (time.time() - vox_start)
                sys.stdout.flush()

    # Print information about how fitting went...
    total_time = time.time() - start_time
    inv_time = total_time - vox_loop_time
    return_params = [best_w_params[:,0:max_features,:],]
    if add_bias:
        return_params += [best_w_params[:,-1,:],]
    else: 
        return_params += [None,]
    print ('\n---------------------------------------')
    print ('total time = %fs' % total_time)
    print ('total throughput = %fs/voxel' % (total_time / n_voxels))
    print ('voxel throughput = %fs/voxel' % (vox_loop_time / n_voxels))
    print ('setup throughput = %fs/model' % (inv_time / n_prfs))
    
    # This step clears the big feature maps for training data from feature extractor (no longer needed)
    _feature_extractor.clear_maps()
    
    best_params = [prf_models[best_prf_models],]+return_params+[features_mean, features_std]+[best_prf_models]
    sys.stdout.flush()

    return best_losses, best_lambdas, best_params
