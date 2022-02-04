"""
Run the model fitting for FWRF model. 
There are a few different versions of fitting in this script, the input arguments tell which kind of fitting to do.
"""

# import basic modules
import sys
import os
import time
import numpy as np
import gc
import torch
import argparse

# import custom modules
code_dir = '/user_data/mmhender/imStat/code/'
sys.path.append(code_dir)
from feature_extraction import fwrf_features, semantic_features, merge_features
from utils import nsd_utils, roi_utils, default_paths, coco_utils

import initialize_fitting as initialize_fitting
import arg_parser as arg_parser
import fwrf_fit, fwrf_predict

device = initialize_fitting.init_cuda()

os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

#################################################################################################
        
    
def fit_fwrf(args):

    model_name, fitting_types = initialize_fitting.get_full_save_name(args)
    output_dir, fn2save = initialize_fitting.get_save_path(model_name, args)
    sys.stdout.flush()
    
    def save_all(fn2save):
    
        """
        Define all the important parameters that have to be saved
        """
        dict2save = {
        'subject': args.subject,
        'volume_space': args.volume_space,
        'fitting_types': fitting_types, 
        'voxel_mask': voxel_mask,
        'brain_nii_shape': brain_nii_shape,
        'image_order': image_order,
        'voxel_index': voxel_index,
        'voxel_roi': voxel_roi,
        'voxel_ncsnr': voxel_ncsnr, 
        'which_prf_grid': args.which_prf_grid,
        'models': prf_models,        
        'best_losses': best_losses,           
        'best_lambdas': best_lambdas,
        'best_params': best_params,       
        'lambdas': lambdas, 
        'val_cc': val_cc,
        'val_r2': val_r2,    
        'partial_masks': partial_masks, 
        'partial_version_names': partial_version_names,        
        'zscore_features': args.zscore_features, 
        'ridge': args.ridge,
        'debug': args.debug,
        'up_to_sess': args.up_to_sess,
        'single_sess': args.single_sess,
        'shuff_rnd_seed': shuff_rnd_seed,
        'use_precomputed_prfs': args.use_precomputed_prfs,
        'saved_prfs_fn': saved_prfs_fn,
        'best_layer_each_voxel': best_layer_each_voxel,
        'saved_best_layer_fn': saved_best_layer_fn,
        }
        # Might be some more things to save, depending what kind of fitting this is
        if args.do_tuning:
            dict2save.update({
            'corr_each_feature': corr_each_feature
            })
        if args.do_sem_disc:
            dict2save.update({
            'sem_discrim_each_axis': sem_discrim_each_axis,
            'sem_corr_each_axis': sem_corr_each_axis,
            'discrim_type_list': discrim_type_list,
            })
        if args.save_pred_data:
            dict2save.update({
            'val_voxel_data': val_voxel_data,
            'val_voxel_data_pred': val_voxel_data_pred,
            'val_image_order': val_image_order,
            })
        if np.any(['semantic' in ft for ft in fitting_types]):
            dict2save.update({
            'semantic_feature_set': args.semantic_feature_set,
            })
        if np.any(['sketch_tokens' in ft for ft in fitting_types]):
            dict2save.update({         
            'use_pca_st_feats': args.use_pca_st_feats, 
            })          
        if np.any(['pyramid' in ft for ft in fitting_types]):
            dict2save.update({
            'use_pca_pyr_feats_hl': args.use_pca_pyr_feats_hl,
            'pyramid_feature_info':pyramid_feature_info,
            'group_all_hl_feats': args.group_all_hl_feats,
            })            
        if np.any(['gabor' in ft for ft in fitting_types]):
            dict2save.update({
            'n_ori_gabor': args.n_ori_gabor,
            'n_sf_gabor': args.n_sf_gabor,
            'gabor_nonlin_fn': args.gabor_nonlin_fn,
            })
        if np.any(['alexnet' in ft for ft in fitting_types]):
            dict2save.update({
            'alexnet_layer_name': args.alexnet_layer_name,
            'alexnet_padding_mode': args.alexnet_padding_mode,
            'use_pca_alexnet_feats': args.use_pca_alexnet_feats, 
            })
        if np.any(['clip' in ft for ft in fitting_types]):
            dict2save.update({
            'clip_layer_name': args.clip_layer_name,
            'clip_model_architecture': args.clip_model_architecture,
            'use_pca_clip_feats': args.use_pca_clip_feats,   
            })

        print('\nSaving to %s\n'%fn2save)
        torch.save(dict2save, fn2save, pickle_protocol=4)

    if (args.do_fitting==False) and (args.date_str==0 or args.date_str=='0' or args.date_str==''):
        raise ValueError('if --do_fitting=False, must specify the date when training result was saved (--date_str).')
    elif (args.do_fitting) and not (args.date_str==0 or args.date_str=='0' or args.date_str==''):
        raise ValueError('if --do_fitting=True, should specify --date_str=0 (rather than entering a date)')    
    if (args.do_sem_disc or args.do_tuning) and not args.do_val:
        raise ValueError('to do tuning analysis or semantic discriminability, need to run validation again (--do_val=True)')       
    if args.shuff_rnd_seed==0:
        shuff_rnd_seed = int(time.strftime('%M%H%d', time.localtime()))       
    else:
        shuff_rnd_seed = args.shuff_rnd_seed
        
    if args.do_tuning:
        corr_each_feature = None
    if args.do_sem_disc:
        sem_discrim_each_axis = None
        sem_corr_each_axis = None
        discrim_type_list = None
    if np.any(['alexnet' in ft for ft in fitting_types]):
        dnn_model='alexnet'
        n_dnn_layers = 5;
        assert(not np.any(['clip' in ft for ft in fitting_types]))
    elif np.any(['clip' in ft for ft in fitting_types]):
        dnn_model='clip'
        n_dnn_layers = 16;
        assert(not np.any(['alexnet' in ft for ft in fitting_types]))
    else:
        dnn_model = None
          
    
    ########## LOADING THE DATA #############################################################################
    # decide what voxels to use  
    voxel_mask, voxel_index, voxel_roi, voxel_ncsnr, brain_nii_shape = \
                                roi_utils.get_voxel_roi_info(args.subject, \
                                args.volume_space, include_all=True, include_body=True)

    if (args.single_sess is not None) and (args.single_sess!=0):
        sessions = np.array([args.single_sess])
    else:
        sessions = np.arange(0,args.up_to_sess)
    # Get all data and corresponding images, in two splits. Always a fixed set that gets left out
    trn_stim_data, trn_voxel_data, val_stim_data, val_voxel_data, \
    image_order, trn_image_order, val_image_order = \
                                nsd_utils.get_data_splits(args.subject, \
                                sessions=sessions, image_inds_only = True, \
                                voxel_mask=voxel_mask, volume_space=args.volume_space, \
                                zscore_betas_within_sess=True, \
                                shuffle_images=args.shuffle_images, random_images=args.random_images, \
                                random_voxel_data=args.random_voxel_data)
    n_voxels = trn_voxel_data.shape[1]   
    
    ########## DEFINE PARAMETERS #############################################################################
    
    holdout_pct=0.10
    holdout_size = int(np.ceil(np.shape(trn_voxel_data)[0]*holdout_pct))   
    lambdas = initialize_fitting.get_lambdas(zscore_features=args.zscore_features, ridge=args.ridge)
    prf_models = initialize_fitting.get_prf_models(which_grid=args.which_prf_grid) 

    sys.stdout.flush()
   
    ########## LOAD PRECOMPUTED PRFS ##########################################################################
        
    if args.use_precomputed_prfs:
        # If we already computed pRFs for this subject on some model, can load those now and use them during 
        # fitting. Faster than fitting pRFs each time.
        best_model_each_voxel, saved_prfs_fn = initialize_fitting.load_precomputed_prfs(args.subject)
        print(trn_voxel_data.shape)
        print(len(best_model_each_voxel))
        assert(len(best_model_each_voxel)==trn_voxel_data.shape[1])
    else:
        # otherwise fitting all params from scratch.
        best_model_each_voxel = None
        saved_prfs_fn = None

    ########### LOOPING OVER VOXEL SUBSETS ############################################################
    # used for clip/alexnet when layer_name is "best_layer", diff voxels get fit w different features
    # otherwise this loop only goes once and voxel_subset_mask is all ones.
    
    # define voxel subsets (if using)      
    if dnn_model is not None and (args.alexnet_layer_name=='best_layer' or args.clip_layer_name=='best_layer'):
        # special case, going to fit groups of voxels separately according to which dnn layer was best
        # creating a list of voxel masks here that will define the subsets to loop over.
        assert(args.do_fitting==True)   # haven't implemented the mid-way resuming in this case
        best_layer_each_voxel, saved_best_layer_fn = \
                  initialize_fitting.load_best_model_layers(args.subject, dnn_model)
        voxel_subset_masks = [best_layer_each_voxel==ll for ll in range(n_dnn_layers)]
        assert(len(best_layer_each_voxel)==n_voxels)
    else:
        # going to fit all voxels w same model
        voxel_subset_masks = [np.ones((n_voxels,), dtype=bool)]
        best_layer_each_voxel = None;
        saved_best_layer_fn = None;
        
    # Start the loop
    for vi, voxel_subset_mask in enumerate(voxel_subset_masks):

        trn_voxel_data_use = trn_voxel_data[:,voxel_subset_mask]
        val_voxel_data_use = val_voxel_data[:,voxel_subset_mask]
        if best_model_each_voxel is not None:
            best_model_each_voxel_use = best_model_each_voxel[voxel_subset_mask]
        else:
            best_model_each_voxel_use = None
        print('voxel mask %d of %d, number of voxels this loop=%d'%(vi, len(voxel_subset_masks), trn_voxel_data_use.shape[1]))
        if trn_voxel_data_use.shape[1]==0:
            print('no voxels, continuing loop')
            continue
            
        ########## CREATE FEATURE LOADERS ###################################################################
        # these help to load sets of pre-computed features in an organized way.
        # first making a list of all the modules of interest (different feature spaces)
        fe = []
        fe_names = []
        for ft in fitting_types:   

            if 'gabor_solo' in ft:
                feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid,\
                                                                feature_type='gabor_solo',\
                                                                n_ori=args.n_ori_gabor, n_sf=args.n_sf_gabor,\
                                                                nonlin_fn=args.gabor_nonlin_fn)
        
                fe.append(feat_loader)
                fe_names.append(ft)
            elif 'pyramid' in ft:
                feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='pyramid_texture',\
                                                                n_ori=args.n_ori_pyr, n_sf=args.n_sf_pyr,\
                                                                include_ll=True, include_hl=True,\
                                                                use_pca_feats_hl = args.use_pca_pyr_feats_hl,\
                                                                do_varpart=args.do_varpart,\
                                                                group_all_hl_feats=args.group_all_hl_feats)       
                fe.append(feat_loader)
                fe_names.append(ft)
                pyramid_feature_info = [feat_loader.feature_column_labels, feat_loader.feature_types_include]

            elif 'sketch_tokens' in ft:
                feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='sketch_tokens',\
                                                                use_pca_feats = args.use_pca_st_feats)
                fe.append(feat_loader)
                fe_names.append(ft)
          
            elif 'alexnet' in ft:
                if args.alexnet_layer_name=='all_conv':
                    names = ['Conv%d_ReLU'%(ll+1) for ll in range(n_dnn_layers)]
                    for ll in range(n_dnn_layers):
                        feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='alexnet',layer_name=names[ll],\
                                                                use_pca_feats = args.use_pca_alexnet_feats,\
                                                                padding_mode = args.alexnet_padding_mode)
                        fe.append(feat_loader)   
                        fe_names.append('alexnet_%s'%names[ll])
                elif args.alexnet_layer_name=='best_layer':
                    this_layer_name = 'Conv%d_ReLU'%(vi+1)
                    print(this_layer_name)
                    feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='alexnet',layer_name=this_layer_name,\
                                                                use_pca_feats = args.use_pca_alexnet_feats,\
                                                                padding_mode = args.alexnet_padding_mode)
                    fe.append(feat_loader)   
                    fe_names.append(ft)
                else:
                    feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='alexnet',layer_name=args.alexnet_layer_name,\
                                                                use_pca_feats = args.use_pca_alexnet_feats,\
                                                                padding_mode = args.alexnet_padding_mode)
                    fe.append(feat_loader)
                    fe_names.append(ft)
          
            elif 'clip' in ft:
                if args.clip_layer_name=='all_resblocks':
                    names = ['block%d'%(ll) for ll in range(n_dnn_layers)]
                    for ll in range(n_dnn_layers):
                        feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='clip',layer_name=names[ll],\
                                                                model_architecture=args.clip_model_architecture,\
                                                                use_pca_feats=args.use_pca_clip_feats)
                        fe.append(feat_loader)   
                        fe_names.append('clip_%s'%names[ll])
                elif args.clip_layer_name=='best_layer':
                    this_layer_name = 'block%d'%(vi)
                    print(this_layer_name)
                    feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='clip',layer_name=this_layer_name,\
                                                                model_architecture=args.clip_model_architecture,\
                                                                use_pca_feats=args.use_pca_clip_feats)
                    fe.append(feat_loader)
                    fe_names.append(ft) 
                else:
                    feat_loader = fwrf_features.fwrf_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_type='clip',layer_name=args.clip_layer_name,\
                                                                model_architecture=args.clip_model_architecture,\
                                                                use_pca_feats=args.use_pca_clip_feats)
                    fe.append(feat_loader)
                    fe_names.append(ft)   
          
            elif 'semantic' in ft:
                this_feature_set = ft.split('semantic_')[1]
                feat_loader = semantic_features.semantic_feature_loader(subject=args.subject,\
                                                                which_prf_grid=args.which_prf_grid, \
                                                                feature_set=this_feature_set,\
                                                                sessions=sessions, \
                                                                shuff_rnd_seed = shuff_rnd_seed, \
                                                                holdout_size = holdout_size)
                fe.append(feat_loader)
                fe_names.append(ft)
          
        # Now combine subsets of features into a single module
        if len(fe)>1:
            feat_loader_full = merge_features.combined_feature_loader(fe, fe_names, do_varpart = args.do_varpart)
        else:
            feat_loader_full = fe[0]

        #### FIT ENCODING MODEL ###################################################################################

        if args.do_fitting:
            gc.collect()
            torch.cuda.empty_cache()
            print('\nStarting training...\n')
            print(len(trn_image_order))

            best_losses_tmp, best_lambdas_tmp, best_weights_tmp, best_biases_tmp, \
                best_prf_models_tmp, features_mean, features_std, \
                best_train_holdout_preds, holdout_trial_order = \
                                fwrf_fit.fit_fwrf_model(trn_image_order, trn_voxel_data_use, \
                                                        feat_loader_full, prf_models, lambdas, \
                                                        best_model_each_voxel = best_model_each_voxel_use, \
                                                        zscore=args.zscore_features, \
                                                        add_bias=True, \
                                                        voxel_batch_size=args.voxel_batch_size, \
                                                        holdout_size=holdout_size, \
                                                        shuffle=True, shuff_rnd_seed=shuff_rnd_seed, \
                                                        device=device, \
                                                        dtype=np.float32, debug=args.debug)
            
            # getting info about how variance partition was set up
            partial_masks_tmp, partial_version_names = feat_loader_full.get_partial_versions()

            # taking the fit params for this set of voxels and putting them into the full array over all voxels
            if vi==0:               
                best_losses = np.zeros((n_voxels, best_losses_tmp.shape[1]), dtype=best_losses_tmp.dtype)
                best_lambdas = np.zeros((n_voxels, best_lambdas_tmp.shape[1]), dtype=best_lambdas_tmp.dtype)
                best_weights = np.zeros((n_voxels, best_weights_tmp.shape[1], \
                                         best_weights_tmp.shape[2]), dtype=best_weights_tmp.dtype)
                best_biases = np.zeros((n_voxels, best_biases_tmp.shape[1]), dtype=best_biases_tmp.dtype)
                best_prf_models = np.zeros((n_voxels, best_prf_models_tmp.shape[1]), \
                                           dtype=best_prf_models_tmp.dtype)
                partial_masks = [[] for ii in range(len(voxel_subset_masks))]        
            
            best_losses[voxel_subset_mask,:] = best_losses_tmp
            best_lambdas[voxel_subset_mask,:] = best_lambdas_tmp
            max_features = feat_loader_full.max_features
            if best_weights.shape[1]<max_features:
                n2pad = max_features - best_weights.shape[1]
                print('padding by %d elements'%n2pad)
                print(np.shape(best_weights))
                best_weights = np.pad(best_weights, [[0,0], [0, n2pad], [0,0]])
                print(np.shape(best_weights))
            best_weights[voxel_subset_mask,0:max_features,:] = best_weights_tmp
            best_biases[voxel_subset_mask,:] = best_biases_tmp
            best_prf_models[voxel_subset_mask,:] = best_prf_models_tmp
            partial_masks[vi] = partial_masks_tmp
            print(partial_masks[vi].shape)
            # "best_params_tmp" will be passed to validation functions (just these voxels)
            # "best_params" will be saved (all voxels)
            best_params_tmp = [prf_models[best_prf_models_tmp,:], best_weights_tmp, best_biases_tmp, \
                               features_mean, features_std, best_prf_models_tmp]
            best_params = [prf_models[best_prf_models,:], best_weights, best_biases, \
                               features_mean, features_std, best_prf_models]
            
            sys.stdout.flush()
            if vi==0:
                val_cc=None
                val_r2=None
                if args.save_pred_data:
                    val_voxel_data_pred=None

            save_all(fn2save)   
            print('\nSaved training results\n')        
            sys.stdout.flush()

        else:

            # stuff that needs to happen if we are resuming this code after the "fit" step but before validation
            print('\nLoading the results of training from %s\n'%fn2save)
            out = torch.load(fn2save)
            best_losses = out['best_losses']
            best_lambdas = out['best_lambdas']
            best_params = out['best_params']
            best_params_tmp = best_params
            
            val_cc = out['val_cc']
            val_r2 = out['val_r2']

            if 'val_voxel_data_pred' in list(out.keys()):
                assert(args.save_pred_data)
                val_voxel_data_pred = out['val_voxel_data_pred']
            if 'corr_each_feature' in list(out.keys()):
                assert(args.do_tuning)
                corr_each_feature = out['corr_each_feature']
            if 'sem_discrim_each_axis' in list(out.keys()):
                assert(args.do_sem_disc)
                sem_discrim_each_axis = out['sem_discrim_each_axis']
                sem_corr_each_axis = out['sem_corr_each_axis']              
                discrim_type_list = out['discrim_type_list']

            shuff_rnd_seed=out['shuff_rnd_seed']

            assert(out['up_to_sess']==args.up_to_sess)
            assert(out['which_prf_grid']==args.which_prf_grid)

            image_size = None
            feat_loader_full.init_for_fitting()
            partial_masks, partial_version_names = feat_loader_full.get_partial_versions()


        ######### VALIDATE MODEL ON HELD-OUT TEST SET ##############################################
        sys.stdout.flush()
        if args.do_val: 
            gc.collect()
            torch.cuda.empty_cache()
            print('about to start validation')
            sys.stdout.flush()
    
            val_cc_tmp, val_r2_tmp, val_voxel_data_pred, features_each_prf = \
                fwrf_predict.validate_fwrf_model(best_params_tmp, prf_models, \
                                                 val_voxel_data_use, val_image_order, \
                                                 feat_loader_full, zscore=args.zscore_features, \
                                                 sample_batch_size=args.sample_batch_size, \
                                                 voxel_batch_size=args.voxel_batch_size, \
                                                 debug=args.debug, \
                                                 dtype=np.float32, device=device)
            if vi==0:
                val_cc = np.zeros((n_voxels, val_cc_tmp.shape[1]), dtype=val_cc_tmp.dtype)
                val_r2 = np.zeros((n_voxels, val_r2_tmp.shape[1]), dtype=val_r2_tmp.dtype)               
            val_cc[voxel_subset_mask,:] = val_cc_tmp
            val_r2[voxel_subset_mask,:] = val_r2_tmp
                
            save_all(fn2save)

        ### ESTIMATE VOXELS' FEATURE TUNING #####################################################################
        sys.stdout.flush()
        if args.do_tuning:

            gc.collect()
            torch.cuda.empty_cache()
            print('about to start feature tuning analysis')
            sys.stdout.flush()
            corr_each_feature_tmp = fwrf_predict.get_feature_tuning(best_params_tmp, features_each_prf, \
                                                                    val_voxel_data_pred, debug=args.debug)
            if vi==0:
                corr_each_feature = np.zeros((n_voxels, corr_each_feature_tmp.shape[1]), dtype=corr_each_feature_tmp.dtype)  
            max_features = feat_loader_full.max_features
            if corr_each_feature.shape[1]<max_features:
                n2pad = max_features - corr_each_feature.shape[1]
                print('padding by %d elements'%n2pad)
                print(np.shape(corr_each_feature))
                corr_each_feature = np.pad(corr_each_feature, [[0,0], [0, n2pad]])
                print(np.shape(corr_each_feature))
            corr_each_feature[voxel_subset_mask,0:max_features] = corr_each_feature_tmp
            
            save_all(fn2save)

        ### ESTIMATE SEMANTIC DISCRIMINABILITY #########################################################################
        sys.stdout.flush()
        if args.do_sem_disc:

            gc.collect()
            torch.cuda.empty_cache()
            print('about to start semantic discriminability analysis')
            sys.stdout.flush()
            labels_all, discrim_type_list, unique_labs_each = \
                    coco_utils.load_labels_each_prf(args.subject, args.which_prf_grid,\
                                                    image_inds=val_image_order, \
                                                    models=prf_models,verbose=False, \
                                                    debug=args.debug)
            discrim_tmp, corr_tmp = \
                    fwrf_predict.get_semantic_discrim(best_params_tmp, \
                                                      labels_all, unique_labs_each, \
                                                      val_voxel_data_pred,\
                                                      debug=args.debug)
            if vi==0:
                sem_discrim_each_axis = np.zeros((n_voxels, discrim_tmp.shape[1]), \
                                                 dtype=discrim_tmp.dtype) 
                sem_corr_each_axis = np.zeros((n_voxels, corr_tmp.shape[1]), \
                                                 dtype=corr_tmp.dtype) 
            sem_discrim_each_axis[voxel_subset_mask,:] = discrim_tmp
            sem_corr_each_axis[voxel_subset_mask,:] = corr_tmp
            
            save_all(fn2save)
            
        # Done!

if __name__ == '__main__':
    
    args = arg_parser.get_args()
    fit_fwrf(args)
   