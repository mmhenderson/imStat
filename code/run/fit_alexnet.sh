#!/bin/bash
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --open-mode=append
#SBATCH --output=./sbatch_output/output-%A-%x-%u.out 
#SBATCH --time=8-00:00:00

source ~/myenv/bin/activate

cd /user_data/mmhender/imStat/code/model_fitting

subjects=(1)

debug=0
up_to_sess=40

sample_batch_size=100
voxel_batch_size=100
zscore_features=1
ridge=1
use_precomputed_prfs=1
which_prf_grid=5
from_scratch=1
date_str=0
do_val=1
do_tuning=1
do_sem_disc=1

fitting_type=alexnet
alexnet_layer_name=best_layer
alexnet_padding_mode=reflect
use_pca_alexnet_feats=1


for subject in ${subjects[@]}
do
    python3 fit_model.py --subject $subject --debug $debug --up_to_sess $up_to_sess --sample_batch_size $sample_batch_size --voxel_batch_size $voxel_batch_size --zscore_features $zscore_features --ridge $ridge --use_precomputed_prfs $use_precomputed_prfs --which_prf_grid $which_prf_grid --from_scratch $from_scratch --date_str $date_str --do_val $do_val --do_tuning $do_tuning --do_sem_disc $do_sem_disc --fitting_type $fitting_type --alexnet_layer_name $alexnet_layer_name  --alexnet_padding_mode $alexnet_padding_mode  --use_pca_alexnet_feats $use_pca_alexnet_feats 

done
