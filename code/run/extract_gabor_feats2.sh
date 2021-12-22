#!/bin/bash
#SBATCH --partition=tarrq
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --open-mode=append
#SBATCH --output=./sbatch_output/output-%A-%x-%u.out 
#SBATCH --time=8-00:00:00

debug=0
use_node_storage=0
n_ori=12
n_sf=8
batch_size=100
which_prf_grid=5
gabor_solo=1
nonlin_fn=1

source ~/myenv/bin/activate

CWD=$(pwd)
cd ../
ROOT=$(pwd)
cd $ROOT/feature_extraction

sublist=(2 3 4 5 6 7 8)
for subject in ${sublist[@]}
do

    python3 extract_gabor_texture_features.py --subject $subject --use_node_storage $use_node_storage --n_ori=$n_ori --n_sf=$n_sf --batch_size $batch_size --debug $debug --which_prf_grid $which_prf_grid --gabor_solo $gabor_solo --nonlin_fn $nonlin_fn
    
done

