# SEND @ ACL 2025 [Openreview Link](https://openreview.net/forum?id=GHtLZZ3yM4&referrer=%5BAuthor%20Console%5D(%2Fgroup%3Fid%3Daclweb.org%2FACL%2F2025%2FConference%2FAuthors%23your-submissions))
Set of experiments to evaluate hallucination on the Pythia suite on multiple checkpoints of one model size.

## MIND pipeline for the activation extraction 
This version of the code uses the MIND framework in order to extract the activations of Pythia models for hallucination explainability on the wikipedia dataset.

## Installation
To install the required packages, create a python3.10 environment (3.10.11 preferred) and run `pip install -r requirements.txt`.

## Usage
1. Generate the data on multiple models for future analysis:
    - Run `python ./MIND/generate_data.py --model_type <1b, 2.4b etc.> --model_family pythia --step_num <1 to 143 for the checkpoints>` to generate the data for the models of the Pythia suite. The script is meant to be run for one model size accross multiple checkpoints.
    - This will generate MIND train, valid, and test json files for the model at the specified checkpoint executed on the truncated inputs. This data will be saved to the `./MIND/auto-labeled/output/` directory.
    - Finally run `python ./MIND/utils/gather_results.py --model_family <> --model_type <>` to gather the results from the generated data. The results will be saved to `./data/diff_results.csv` and is the file that shows the wikipedia examples on which the models hallucinate in an oscillatory manner. 
2. Extract the activations from the generated data:
    - Run `python ./MIND/generate_hd_chunk.py` which automatically looks at the directories of the generated results (3 checkpoints for now) and extracts the activations for the hallucination examples. The activations are compiled in one json file for each data point and saved in the `./data/hidden_layer_activations.json` files.
3. The activations can be used to generate the explanations for the hallucinations over the training process of a model.

- You can look at the `generate_data.sh` and `post_processing.sh` scripts to see how the scripts are run consecutively.

## Running the experiments with HPC
- Load the python environment as mentioned above, and follow the BATCHing instructions in the `generate_data.sh` and `post_processing.sh` scripts to run the experiments on the HPC.

## Running other experiments 
- `send` directory is for the implementation of the SeND training technique for hallucination detection. For more information you can refer to the README of the directory.
- `rag_experiments` directory is for the experiments related to integrating RAG with SeND and testing SeND with additional hallucination detection metrics.
- `sensitivity` directory contains the source code for EES calculation and algorithm imlpementation.


For most experiments, if done with cloud resources, 2 A100l or V100 GPUs will be optimal and not much memory is needed.

```bibtex
@inproceedings{
anonymous2025hallucination,
title={Hallucination Detox: Sensitivity Dropout (SenD) for Large Language Model Training},
author={Anonymous},
booktitle={The 63rd Annual Meeting of the Association for Computational Linguistics},
year={2025},
url={https://openreview.net/forum?id=GHtLZZ3yM4}
}
```
