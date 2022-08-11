import argparse
import numpy as np
import sys, os
import torch
import time
import h5py

#import custom modules
from utils import prf_utils, torch_utils, texture_utils, default_paths
from model_fitting import initialize_fitting

if torch.cuda.is_available():
    device = initialize_fitting.init_cuda()
else:
    device = 'cpu:0'
        
# This code applies pRF weights to each sketch tokens feature map, to get final features
# in each pRF. Before running this, need to use the matlab code get_st_features.m to get 
# the full feature maps.


def get_features_each_prf(features_file, models, mult_patch_by_prf=True, do_avg_pool=True, \
                          batch_size=100, aperture=1.0, debug=False, device=None):
    """
    Extract the portion of the feature maps corresponding to each prf in 'models'
    Start with loading the feature maps h5py file (generated by get_st_features.m)
    Save smaller features as an h5py file [n_images x n_features x n_prfs]
    """
    if device is None:
        device = 'cpu:0'
        
    with h5py.File(features_file, 'r') as data_set:
        ds_size = data_set['/features'].shape
    n_images = ds_size[3]
    n_features = ds_size[0]
    map_resolution = ds_size[1]
    n_prfs = models.shape[0]
    features_each_prf = np.zeros((n_images, n_features, n_prfs))
    n_batches = int(np.ceil(n_images/batch_size))

    for bb in range(n_batches):

        if debug and bb>1:
            continue

        batch_inds = np.arange(batch_size * bb, np.min([batch_size * (bb+1), n_images]))

        print('Loading features for images [%d - %d]'%(batch_inds[0], batch_inds[-1]))
        st = time.time()
        with h5py.File(features_file, 'r') as data_set:
            # Note this order is reversed from how it was saved in matlab originally.
            # The dimensions go [features x h x w x images]
            # Luckily h and w are swapped matlab to python anyway, so can just switch the first and last.
            values = np.copy(data_set['/features'][:,:,:,batch_inds])
            data_set.close()  
        fmaps_batch = np.moveaxis(values, [0,1,2,3],[3,1,2,0])

        elapsed = time.time() - st
        print('Took %.5f sec to load feature maps'%elapsed)

        maps_full_field = torch_utils._to_torch(fmaps_batch, device=device)

        for mm in range(n_prfs):

            if debug and mm>1:
                continue

            prf_params = models[mm,:]
            x,y,sigma = prf_params
            print('Getting features for pRF [x,y,sigma]:')
            print([x,y,sigma])
            n_pix = map_resolution

            # Define the RF for this "model" version
            prf = torch_utils._to_torch(prf_utils.gauss_2d(center=[x,y], sd=sigma, \
                               patch_size=n_pix, aperture=aperture, dtype=np.float32), device=device)
            minval = torch.min(prf)
            maxval = torch.max(prf-minval)
            prf_scaled = (prf - minval)/maxval

            if mult_patch_by_prf:         
                # This effectively restricts the spatial location, so no need to crop
                maps = maps_full_field * prf_scaled.view([1,map_resolution,map_resolution,1])
            else:
                # This is a coarser way of choosing which spatial region to look at
                # Crop the patch +/- n SD away from center
                n_prf_sd_out = 2
                bbox = texture_utils.get_bbox_from_prf(prf_params, prf.shape, \
                               n_prf_sd_out, min_pix=None, verbose=False, force_square=False)
                print('bbox to crop is:')
                print(bbox)
                maps = maps_full_field[:,bbox[0]:bbox[1], bbox[2]:bbox[3],:]

            if do_avg_pool:
                features_batch = torch.mean(maps, dim=(1,2))
            else:
                features_batch = torch.max(maps, dim=(1,2))
                
            print('model %d, min/max of features in batch: [%s, %s]'%(mm, \
                                  torch.min(features_batch), torch.max(features_batch))) 

            features_each_prf[batch_inds,:,mm] = torch_utils.get_value(features_batch)
                      
    return features_each_prf


def proc_one_subject(subject, args):
    
    if args.use_node_storage:
        sketch_token_feat_path = default_paths.sketch_token_feat_path_localnode
    else:
        sketch_token_feat_path = default_paths.sketch_token_feat_path
    if args.debug:      
        save_path = os.path.join(sketch_token_feat_path,'DEBUG')
        sketch_token_feat_path = os.path.join(sketch_token_feat_path,'DEBUG')
    else:
        save_path = sketch_token_feat_path
        
    if not os.path.exists(save_path):
        os.makedirs(save_path)
        
    # Params for the spatial aspect of the model (possible pRFs)
    models = initialize_fitting.get_prf_models(which_grid=args.which_prf_grid)    
    
    # These params are fixed
    map_resolution = 240
    n_prf_sd_out = 2
    mult_patch_by_prf = True
    do_avg_pool = True
 
    if args.grayscale:
        features_file = os.path.join(sketch_token_feat_path, \
                            'S%d_features_grayscale_%d.h5py'%(subject, map_resolution))
        filename_save = os.path.join(save_path, \
                           'S%d_features_grayscale_each_prf_grid%d.h5py'%(subject, args.which_prf_grid))

    else:
        features_file = os.path.join(sketch_token_feat_path, \
                            'S%d_features_%d.h5py'%(subject, map_resolution))
        filename_save = os.path.join(save_path, \
                            'S%d_features_each_prf_grid%d.h5py'%(subject, args.which_prf_grid))

    if not os.path.exists(features_file):
        raise RuntimeError('Looking at %s for precomputed features, not found.'%features_file)
        
    features_each_prf = get_features_each_prf(features_file, models, \
                            mult_patch_by_prf=mult_patch_by_prf, \
                            do_avg_pool=do_avg_pool, batch_size=args.batch_size, aperture=1.0, \
                            debug=args.debug, device=device)

    
    save_features(features_each_prf, filename_save)

    if args.rm_big==1:
        
        edges_file = features_file.split('_features_')[0] + '_edges_' + features_file.split('_features_')[1]
        print('removing raw file from %s'%features_file)
        os.remove(features_file)
        print('removing raw file from %s'%edges_file)
        os.remove(edges_file)
        
        print('done removing')
        
    
def proc_other_image_set(image_set, args):
    
    if args.use_node_storage:
        sketch_token_feat_path = default_paths.sketch_token_feat_path_localnode
    else:
        sketch_token_feat_path = default_paths.sketch_token_feat_path
    if args.debug:
        sketch_token_feat_path = os.path.join(sketch_token_feat_path,'DEBUG')
        save_path = os.path.join(sketch_token_feat_path,'DEBUG')
    else:
        save_path = sketch_token_feat_path
        
    if not os.path.exists(save_path):
        os.makedirs(save_path)
        
    # Params for the spatial aspect of the model (possible pRFs)
    models = initialize_fitting.get_prf_models(which_grid=args.which_prf_grid)    
    
    # These params are fixed
    map_resolution = 240
    n_prf_sd_out = 2
    mult_patch_by_prf = True
    do_avg_pool = True
  
    if args.grayscale:
        features_file = os.path.join(sketch_token_feat_path, \
                           '%s_features_grayscale_%d.h5py'%(image_set, map_resolution))
        filename_save = os.path.join(save_path, \
                           '%s_features_grayscale_each_prf_grid%d.h5py'%(image_set, args.which_prf_grid))
    else:
        features_file = os.path.join(sketch_token_feat_path, \
                           '%s_features_%d.h5py'%(image_set, map_resolution))
        filename_save = os.path.join(save_path, \
                           '%s_features_each_prf_grid%d.h5py'%(image_set, args.which_prf_grid))

    if not os.path.exists(features_file):
        raise RuntimeError('Looking at %s for precomputed features, not found.'%features_file)
        
    features_each_prf = get_features_each_prf(features_file, models, \
                            mult_patch_by_prf=mult_patch_by_prf, \
                            do_avg_pool=do_avg_pool, batch_size=args.batch_size, aperture=1.0, \
                            debug=args.debug, device=device)

    
    
    save_features(features_each_prf, filename_save)
    
    if args.rm_big==1:
        
        edges_file = features_file.split('_features_')[0] + '_edges_' + features_file.split('_features_')[1]
        print('removing raw file from %s'%features_file)
        os.remove(features_file)
        print('removing raw file from %s'%edges_file)
        os.remove(edges_file)
        
        print('done removing')
    
def save_features(features_each_prf, filename_save):
    
    print('Writing prf features to %s\n'%filename_save)
    
    t = time.time()
    with h5py.File(filename_save, 'w') as data_set:
        dset = data_set.create_dataset("features", np.shape(features_each_prf), dtype=np.float32)
        data_set['/features'][:,:,:] = features_each_prf
        data_set.close()  
    elapsed = time.time() - t
    
    print('Took %.5f sec to write file'%elapsed)
    

    
if __name__ == '__main__':
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--subject", type=int,default=0,
                    help="number of the subject, 1-8")
    parser.add_argument("--image_set", type=str,default='none',
                    help="name of the image set to use (if not an NSD subject)")
    parser.add_argument("--use_node_storage", type=int,default=0,
                    help="want to save and load from scratch dir on current node? 1 for yes, 0 for no")
    parser.add_argument("--debug", type=int,default=0,
                    help="want to run a fast test version of this script to debug? 1 for yes, 0 for no")
    parser.add_argument("--which_prf_grid", type=int,default=1,
                    help="which version of prf grid to use")
    parser.add_argument("--batch_size", type=int,default=100,
                    help="batch size to use for feature extraction")
    parser.add_argument("--grayscale", type=int,default=0,
                    help="use features computed from grayscale images only? 1 for yes, 0 for no")
    parser.add_argument("--rm_big", type=int,default=0,
                    help="want to remove big feature maps files when done? 1 for yes, 0 for no")

    args = parser.parse_args()
    
    if args.subject==0:
        args.subject=None
    if args.image_set=='none':
        args.image_set=None
                         
    args.debug = (args.debug==1)     
    args.grayscale = (args.grayscale==1)     
    
    print(args.subject)
    print(args.image_set)
    
    if args.subject is not None:
        
        proc_one_subject(subject = args.subject, args=args)
        
    elif args.image_set is not None:
        
        proc_other_image_set(image_set=args.image_set, args=args)
        
    
