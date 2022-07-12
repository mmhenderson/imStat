#!/bin/bash
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --exclude=mind-1-23
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --open-mode=append
#SBATCH --output=./sbatch_output/output-%A-%x-%u.out 
#SBATCH --time=8-00:00:00

echo $SLURM_JOBID
echo $SLURM_NODELIST

source ~/myenv/bin/activate

cd /user_data/mmhender/modfit/code/model_fitting

# subjects=(5)
subjects=(1 2 3 4 5 6 7 8)
# subjects=(1)
# 
debug=0
up_to_sess=40
# debug=1
# up_to_sess=1

shuffle_images_once=0

average_image_reps=1

sample_batch_size=1000
voxel_batch_size=1000
zscore_features=1
ridge=1
# use_precomputed_prfs=1
use_precomputed_prfs=0
prfs_model_name=texture

which_prf_grid=5
from_scratch=1
date_str=0
overwrite_sem_disc=1
do_val=1
do_tuning=0
do_sem_disc=0

do_pyr_varpart=0

fitting_type=texture_pyramid

n_ori_pyr=4
n_sf_pyr=4
group_all_hl_feats=1

pyr_pca_type=pcaHL

set_lambda_per_group=1

sigmas=(0.020 0.031 0.048 0.074 0.114 0.176 0.271 0.419 0.647 1.000)


for subject in ${subjects[@]}
do
    for prf_fixed_sigma in ${sigmas[@]}
    do
   
        python3 fit_model.py --subject $subject --debug $debug --up_to_sess $up_to_sess --average_image_reps $average_image_reps --sample_batch_size $sample_batch_size --voxel_batch_size $voxel_batch_size --zscore_features $zscore_features --ridge $ridge --use_precomputed_prfs $use_precomputed_prfs --prfs_model_name $prfs_model_name --which_prf_grid $which_prf_grid --from_scratch $from_scratch --date_str $date_str --do_val $do_val --do_tuning $do_tuning --do_sem_disc $do_sem_disc --overwrite_sem_disc $overwrite_sem_disc --fitting_type $fitting_type --n_ori_pyr $n_ori_pyr --n_sf_pyr $n_sf_pyr --group_all_hl_feats $group_all_hl_feats --do_pyr_varpart $do_pyr_varpart --pyr_pca_type $pyr_pca_type --set_lambda_per_group $set_lambda_per_group --prf_fixed_sigma $prf_fixed_sigma 
    
    done
    
done
