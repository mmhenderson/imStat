# import basic modules
import sys
import os
import time
import numpy as np
import argparse

# import custom modules
from utils import nsd_utils
from feature_extraction import default_feature_loaders
import initialize_fitting

def balance_orient_vs_categories(subject, which_prf_grid=5, axes_to_do=[0,2,3], n_samp_iters=1000, \
                                 debug=False):
   
    models = initialize_fitting.get_prf_models(which_grid = which_prf_grid)   
    n_prfs = models.shape[0]

    # figure out what images are available for this subject - assume that we 
    # will be using all the available sessions. 
    subject_df = nsd_utils.get_subj_df(subject)
    image_order = nsd_utils.get_master_image_order()    
    session_inds = nsd_utils.get_session_inds_full()
    sessions = np.arange(nsd_utils.max_sess_each_subj[subject-1])
    inds2use = np.isin(session_inds, sessions) # remove any sessions that weren't shown
    # list of all the image indices shown on each trial
    image_order = image_order[inds2use] 
    # reduce to the 10,000 unique images
    # NOTE that this order will only work correctly if we average over image repetitions
    image_order = np.unique(image_order) 
    # will balance the trn/val sets separately
    # trninds and valinds are boolean, same length as image_order
    trninds = np.array(subject_df['shared1000']==False)[image_order]
    valinds = np.array(subject_df['shared1000']==True)[image_order]
    n_trials = len(image_order)
    n_trn_trials = np.sum(trninds)
    n_val_trials = np.sum(valinds)
    
    # get semantic category labels
    labels_all, discrim_type_list, unique_labels_each = \
                        initialize_fitting.load_labels_each_prf(subject, \
                                which_prf_grid, image_inds=image_order, \
                                models=models,verbose=False, debug=debug)
    labels_all_trn = labels_all[trninds,:,:]
    labels_all_val = labels_all[valinds,:,:]
    
    # create feature loader
    feat_loaders, path_to_load = \
        default_feature_loaders.get_feature_loaders([subject], \
                                                    feature_type='gabor_solo',\
                                                    which_prf_grid=which_prf_grid)
    feat_loader = feat_loaders[0]

    # boolean masks for which trials will be included in the balanced sets
    trninds_mask = np.zeros((n_trn_trials,n_samp_iters,n_prfs,len(axes_to_do)), dtype=bool)
    valinds_mask = np.zeros((n_val_trials,n_samp_iters,n_prfs,len(axes_to_do)), dtype=bool)
    
    # all_resample_inds_trn = [[] for mm in range(n_prfs)]
    min_counts_trn = np.zeros((n_prfs,len(axes_to_do)))
    # all_resample_inds_val = [[] for mm in range(n_prfs)]
    min_counts_val = np.zeros((n_prfs,len(axes_to_do)))

    # will put the 12 orientations into four equal sized bins. 
    # centered at 0, 45, 90, 135 deg
    n_ori_bins=4;
    bin_values = np.array([0,0,1,1,1,2,2,2,3,3,3,0],dtype=int);
    unique_ori = np.arange(n_ori_bins);
    n_ori=12;n_sf=8;
    # unique_ori = np.arange(n_ori);
    n_categ=2;
    unique_categ = np.arange(n_categ);

    for mm in range(n_prfs):

        # all_resample_inds_trn[mm] = [[] for aa in range(len(axes_to_do))]
        # all_resample_inds_val[mm] = [[] for aa in range(len(axes_to_do))]

        if debug and mm>1:
            continue

        print('processing pRF %d of %d'%(mm, n_prfs))
        sys.stdout.flush()
        # load features for this set of images 
        features, _ = feat_loader.load(image_order,mm);
        features_reshaped = np.reshape(features, [n_trials, n_ori, n_sf], order='F')
        features_each_orient = np.mean(features_reshaped, axis=2)
        
        max_orient = np.argmax(features_each_orient, axis=1).astype(int)
        orient_labels = bin_values[max_orient]
        orient_labels_trn = orient_labels[trninds]
        orient_labels_val = orient_labels[valinds]
        
#         features_trn = features[trninds,:]
#         features_val = features[valinds,:]
#         # compute average power at each orientation
#         features_reshaped_trn = np.reshape(features_trn, [np.sum(trninds), n_ori, n_sf], order='F')
#         features_each_orient_trn = np.mean(features_reshaped_trn, axis=2)
#         features_reshaped_val = np.reshape(features_val, [np.sum(valinds), n_ori, n_sf], order='F')
#         features_each_orient_val = np.mean(features_reshaped_val, axis=2)
#         # choose the max orientation for each image. 
#         # Will use this as the "label" to balance over
#         max_orient_trn = np.argmax(features_each_orient_trn, axis=1).astype(int)
#         max_orient_val = np.argmax(features_each_orient_val, axis=1).astype(int)
        
        for ai, aa in enumerate(axes_to_do):

            print([ai, aa])
            # labels for whatever semantic axis is of interest
            categ_labels_trn = labels_all_trn[:,aa,mm]  
            categ_labels_val = labels_all_val[:,aa,mm]  

            trial_inds_resample_trn, min_count_trn = \
                    get_balanced_trials(orient_labels_trn, \
                                        categ_labels_trn, n_samp_iters=n_samp_iters, \
                                        unique1=unique_ori,unique2=unique_categ)
            trial_inds_resample_val, min_count_val = \
                    get_balanced_trials(orient_labels_val, \
                                        categ_labels_val, n_samp_iters=n_samp_iters, \
                                        unique1=unique_ori,unique2=unique_categ)
            
            # check a few of the trial lists just to make sure this worked
            if min_count_trn is not None:
                u, counts = np.unique(orient_labels_trn[trial_inds_resample_trn[1,:]], return_counts=True)
                assert(np.all(u==unique_ori))
                assert(np.all(counts==min_count_trn*n_categ))
                u, counts = np.unique(categ_labels_trn[trial_inds_resample_trn[1,:]], return_counts=True)
                assert(np.all(u==unique_categ))
                assert(np.all(counts==min_count_trn*n_ori_bins))
                
            if min_count_val is not None:
                u, counts = np.unique(orient_labels_val[trial_inds_resample_val[1,:]], return_counts=True)
                assert(np.all(u==unique_ori))
                assert(np.all(counts==min_count_val*n_categ))
                u, counts = np.unique(categ_labels_val[trial_inds_resample_val[1,:]], return_counts=True)
                assert(np.all(u==unique_categ))
                assert(np.all(counts==min_count_val*n_ori_bins))

            # put the numeric trial indices into boolean mask arrays
            for xx in range(n_samp_iters):
                if trial_inds_resample_trn is not None:
                    trninds_mask[trial_inds_resample_trn[xx,:],xx,mm,ai] = 1
                else:
                    trninds_mask[:,xx,mm,ai]
                if trial_inds_resample_val is not None:
                    valinds_mask[trial_inds_resample_val[xx,:],xx,mm,ai] = 1
                else:
                    valinds_mask[:,xx,mm,ai] = 1
                
            # all_resample_inds_trn[mm][ai] = trial_inds_resample_trn
            min_counts_trn[mm,ai] = min_count_trn
            
            # all_resample_inds_val[mm][ai] = trial_inds_resample_val
            min_counts_val[mm,ai] = min_count_val

    # saving the results, one file per semantic axis of interest
    for ai, aa in enumerate(axes_to_do):
        print([ai, aa])
        print(discrim_type_list[aa])
        fn2save = os.path.join(path_to_load, \
                           'S%d_trial_resamp_order_balance_4orientbins_%s.npy'\
                               %(subject, discrim_type_list[aa]))
        # fn2save = os.path.join(path_to_load, \
                           # 'S%d_trial_resamp_order_balance_12orient_%s.npy'\
                               # %(subject, discrim_type_list[aa]))
        # trial_inds_save_trn = [all_resample_inds_trn[mm][ai] for mm in range(n_prfs)]
        # trial_inds_save_val = [all_resample_inds_val[mm][ai] for mm in range(n_prfs)]
        print('saving to %s'%fn2save)
        np.save(fn2save, {'trial_inds_trn': trninds_mask[:,:,:,ai], \
                          'min_counts_trn': min_counts_trn[:,ai], \
                          'trial_inds_val': valinds_mask[:,:,:,ai], \
                          'min_counts_val': min_counts_val[:,ai], \
                          'image_order': image_order, \
                          'trninds': trninds, \
                          'valinds': valinds}, 
                allow_pickle=True)

def get_balanced_trials(labels1, labels2, n_samp_iters=1000, \
                        unique1=None, unique2=None, rndseed=None):
    
    if unique1 is None:
        unique1 = np.unique(labels1)
    if unique2 is None:
        unique2 = np.unique(labels2)
    unique1 = unique1[~np.isnan(unique1)]
    unique2 = unique2[~np.isnan(unique2)]
    n1 = len(unique1)
    n2 = len(unique2)
    n_bal_groups = n1*n2
   
    
    labels_balance = np.array([labels1, labels2]).T
    # list the unique combinations of the label values (groups to balance)
    combs = np.array([np.tile(unique1,[n2,]), np.repeat(unique2, n1),]).T
  
    counts_orig = np.array([np.sum(np.all(labels_balance==ii, axis=1)) \
                       for ii in combs])
    min_count = np.min(counts_orig)
    
    if min_count==0:
        print('missing at least one group of labels')
        # at least one group is missing, this won't work
        return None, None
    
    # make an array of the indices to use - [n_samp_iters x n_resampled_trials]
    trial_inds_resample = np.zeros((n_samp_iters, min_count*n_bal_groups),dtype=int)
    
    if rndseed is None:
        rndseed = int(time.strftime('%M%H%d', time.localtime()))
    np.random.seed(rndseed)
    
    for gg in range(n_bal_groups):
        # find actual list of trials with this label combination
        trial_inds_this_comb = np.where(np.all(labels_balance==combs[gg,:], axis=1))[0]
        samp_inds = np.arange(gg*min_count, (gg+1)*min_count)
        for ii in range(n_samp_iters):
            # sample without replacement from the full set of trials.
            # if this is the smallest group, this means taking all the trials.
            # otherwise it is a sub-set of the trials.
            trial_inds_resample[ii,samp_inds] = np.random.choice(trial_inds_this_comb, \
                                                                 min_count, \
                                                                 replace=False)

    return trial_inds_resample, min_count
    
        
if __name__ == '__main__':
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--subject", type=int,default=1,
                    help="number of the subject, 1-8")
    parser.add_argument("--n_samp_iters", type=int,default=1000,
                    help="how many balanced trial orders to create?")
    parser.add_argument("--which_prf_grid", type=int,default=5,
                    help="which grid of candidate prfs?")     
    parser.add_argument("--debug",type=int,default=0,
                    help="want to run a fast test version of this script to debug? 1 for yes, 0 for no")
    
    args = parser.parse_args()
    
    balance_orient_vs_categories(args.subject, args.which_prf_grid, \
                                 axes_to_do=[0,2,3], debug=args.debug==1, \
                                 n_samp_iters=args.n_samp_iters)
   