import torch
import numpy as np
from sensitivity.most_sensitive import load_model
from torch.utils.data import Dataset, DataLoader
import torch.optim as optim
from tqdm import tqdm
import pandas as pd
import wandb
from sensitivity.efficient_eigenscore.efficient import *
import argparse

# Initialize argument parser
argument_parser = argparse.ArgumentParser()
argument_parser.add_argument("--dataset_name", type=str, required=True, help="Name of the dataset to use.")
argument_parser.add_argument("--k", type=float, required=True, help="Percentage of top sensitive neurons to return.")
args = argument_parser.parse_args()

# Set epoch threshold to fixed value
epoch_threshold = 3

# Load model and tokenizer
MODEL_NAME = "EleutherAI/pythia-1B"
model, tokenizer = load_model(MODEL_NAME, 143000)
tokenizer.pad_token = tokenizer.eos_token

class TextDataset(Dataset):
    def __init__(self, texts, tokenizer, max_length=512):
        self.texts = texts
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        text = self.texts[idx]
        tokens = self.tokenizer(
            text,
            return_tensors='pt',
            max_length=self.max_length,
            padding='max_length',
            truncation=True
        )
        input_ids = tokens['input_ids'].squeeze(0)
        attention_mask = tokens['attention_mask'].squeeze(0)
        # For language modeling, the labels are typically the input_ids shifted by one
        labels = input_ids.clone()
        return input_ids, attention_mask, labels

# Load the actual data
input_data_dir = f'send/data/{args.dataset_name}.csv'
data = pd.read_csv(input_data_dir)
all_data = data['texts'].tolist()[:200]

# Randomly select 80 samples to go in the large dataset and 20 for the tracking dataset
total_data = len(all_data)
large_texts_end = int(0.8 * total_data)
tracking_texts_end = int(0.9 * total_data)

large_texts = all_data[:large_texts_end]
tracking_texts = all_data[large_texts_end:tracking_texts_end]
test_texts = all_data[tracking_texts_end:]

# Instantiate datasets
large_dataset = TextDataset(large_texts, tokenizer)
tracking_dataset = TextDataset(tracking_texts, tokenizer)

large_dataloader = DataLoader(large_dataset, batch_size=1, shuffle=True)   
tracking_dataloader = DataLoader(tracking_dataset, batch_size=1, shuffle=False)

# Define optimizer and loss function
optimizer = optim.AdamW(model.parameters(), lr=1e-4)
criterion = torch.nn.CrossEntropyLoss()

# Set the device to GPU if available
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')  
print("starting training")

# Number of epochs
num_epochs = 20

# Initialize Weights & Biases
wandb.init(project=f"pythia-send-{args.dataset_name}-k-ablation", config={
    "model_name": MODEL_NAME,
    "epochs": num_epochs,
    "batch_size": 1,
    "learning_rate": 1e-4,
    "deterministic_dropout_rate": args.k * 100,
    "dataset_size": len(large_dataset) + len(tracking_dataset),
    "device": device.type,
    "eigenscore_type": "EES",
    "epoch_threshold": epoch_threshold  # Log epoch threshold
})
wandb.watch(model, log="all")

def pad_embeddings(embeddings, target_dim=2048):
    """
    Pad embeddings to the target dimension.

    Args:
    embeddings (np.array): Embeddings of shape (num_datapoints, num_dim).
    target_dim (int): Target dimension to pad to.

    Returns:
    np.array: Padded embeddings of shape (num_datapoints, target_dim).
    """
    num_datapoints, num_dim = embeddings.shape
    if num_dim < target_dim:
        padding = np.zeros((num_datapoints, target_dim - num_dim))
        padded_embeddings = np.concatenate((embeddings, padding), axis=1)
    else:
        padded_embeddings = embeddings
    return padded_embeddings

def net_change(embeddings: np.ndarray):
    return np.sum(np.abs(np.diff(embeddings, axis=0)), axis=0)

def net_variability(embeddings: np.ndarray):
    variability = np.var(embeddings, axis=0)
    return variability

def combined_sensitivity(embeddings: np.ndarray):
    net_change_comp = net_change(embeddings)
    net_variability_comp = net_variability(embeddings)
    return net_change_comp * net_variability_comp

def top_k_percent_sensitive(net_change: np.ndarray, k: float):
    net_change = net_change[0]
    num_elements = int(k * len(net_change))
    sorted_indices = np.argsort(net_change)[::-1]
    top_indices = sorted_indices[:num_elements]
    top_changes = {index: net_change[index] for index in top_indices}
    return top_changes

def get_sensitive_neurons(embeddings_list, k=args.k):
    """
    Identify sensitive neurons based on the average embeddings across epochs.

    Args:
    embeddings_list (list of np.array): List of embeddings of shape (3, num_datapoints, 2048).
    k (float): Percentage of top sensitive neurons to return.

    Returns:
    dict: Indices and sensitivity values of the top k% sensitive neurons.
    """
    # Convert list to numpy array for easier manipulation
    embeddings_array = np.array(embeddings_list)  # Shape: (3, num_datapoints, 2048)
    
    # Calculate the average embeddings across epochs
    avg_embeddings = np.mean(embeddings_array, axis=0)  # Shape: (num_datapoints, 2048)
    
    # Compute the combined sensitivity
    sensitivity = combined_sensitivity(avg_embeddings)
    
    # Identify the top k% sensitive neurons
    top_sensitive_neurons = top_k_percent_sensitive(sensitivity, k)
    print(top_sensitive_neurons)
    
    return top_sensitive_neurons

def drop_sensitive_neurons(model, sensitive_neurons):
    with torch.no_grad():
        for neuron in sensitive_neurons:
            model.gpt_neox.layers[-2].mlp.dense_h_to_4h.weight[neuron, :] = 0
            model.gpt_neox.layers[-2].mlp.dense_h_to_4h.bias[neuron] = 0
            model.gpt_neox.layers[-2].mlp.dense_4h_to_h.weight[:, neuron] = 0
            model.gpt_neox.layers[-2].mlp.dense_4h_to_h.bias[neuron] = 0

def compute_eigenscore(embedding_matrix, alpha=0.001):
    """
    Compute the eigenscore of the embedding matrix.
    
    Args:
    embedding_matrix (np.array): The embedding matrix.
    alpha (float): Regularization parameter.
    
    Returns:
    float: The eigenscore.
    """
    # Mean center the data
    embedding_matrix = embedding_matrix - np.mean(embedding_matrix, axis=0)
    n = embedding_matrix.shape[0]
    _, S, _ = np.linalg.svd(embedding_matrix, full_matrices=False)  # Main source of complexity
    lambda_reg = (S**2 / (n - 1)) + alpha
    eigenscore = np.sum(np.log(lambda_reg))
    return eigenscore / n

def compute_efficient_eigenscore(X, limit=20, alpha=0.001):
    X = X[:, :, 0] if X.ndim == 3 else X  # Ensure X has the correct shape
    # Mean-center the data
    X_mean = np.mean(X, axis=0, keepdims=True)
    X_centered = X - X_mean
    
    # Standardize the data
    X_std = np.std(X_centered, axis=0, keepdims=True)
    X_normalized = X_centered / X_std
    
    # Compute the largest singular value using the power method
    sigma_max = power_method(X_normalized)
    X_normalized /= sigma_max

    # Compute Chebyshev coefficients
    c_m_values = np.array([compute_c_m(m) for m in range(1, limit + 1)])
    
    # Number of columns and stochastic trace estimation samples
    K = X.shape[1]
    Nz = 20
    Z = np.random.randn(K, Nz)
    
    # Compute T_prime_m values using Chebyshev polynomials
    T_prime_m_values = compute_T_prime_m(X_normalized, limit, Z)
    
    # Compute d_m values
    d_m_values = np.array([compute_dm(T_prime_m_values, Z, m) for m in range(1, limit + 1)])
    
    # Regularize the singular values
    n = X.shape[0]
    _, S, _ = np.linalg.svd(X_normalized, full_matrices=False)
    lambda_reg = (S**2 / (n - 1)) + alpha
    
    # Compute the eigenscore
    eigenscore = np.sum(d_m_values * c_m_values)
    eigenscore += np.sum(np.log(lambda_reg))
    
    return eigenscore / n

def get_embeddings(model, tokenizer, test_texts, num_passes=10):
    """
    Get the second-to-last hidden state embeddings for each text in test_texts.
    
    Args:
    model (torch.nn.Module): The model.
    tokenizer (callable): The tokenizer.
    test_texts (list): List of texts to get embeddings for.
    num_passes (int): Number of forward passes.
    
    Returns:
    np.array: The embeddings.
    """
    device = next(model.parameters()).device  # Get the device of the model
    model.eval()
    embeddings = []
    with torch.no_grad():
        for _ in range(num_passes):
            for text in test_texts:
                inputs = tokenizer(text, return_tensors='pt').to(device)  # Move inputs to the same device as the model
                outputs = model(**inputs)
                hidden_states = outputs.hidden_states
                SLT_embedding = hidden_states[-2][:, -2, :]  # Second-to-last hidden state
                embeddings.append(SLT_embedding.cpu().numpy())  # Move to CPU before converting to numpy
    return np.array(embeddings)

# Initialize list to store embeddings for the last 3 epochs
embeddings_list = []

# Variable to store neurons to be dropped across multiple epochs
current_sensitive_neurons = None
dropout_applied_epochs = 0

for epoch in range(num_epochs):
    model.train()
    running_loss = 0.0
    
    # Apply previously computed sensitive neurons for dropout
    if current_sensitive_neurons is not None and dropout_applied_epochs < epoch_threshold:
        drop_sensitive_neurons(model, current_sensitive_neurons)
        dropout_applied_epochs += 1
        wandb.log({
            "epoch": epoch + 1,
            "num_sensitive_neurons": len(current_sensitive_neurons),
            "dropout_epochs_remaining": epoch_threshold - dropout_applied_epochs
        })
    else:
        # Reset if epoch_threshold epochs have passed with the same dropout
        current_sensitive_neurons = None
        dropout_applied_epochs = 0

    # Training on the large dataset
    for batch in tqdm(large_dataloader, desc="Training batches", leave=False):
        input_ids, attention_mask, labels = batch
        input_ids, attention_mask, labels = input_ids.to(device), attention_mask.to(device), labels.to(device)
        
        optimizer.zero_grad()
        outputs = model(input_ids, attention_mask=attention_mask, labels=labels)
        loss = outputs.loss
        
        loss.backward()
        optimizer.step()
        
        running_loss += loss.item()
        wandb.log({
            "epoch": epoch + 1,
            "batch_loss": loss.item()
        })

    epoch_loss = running_loss / len(large_dataloader)
    print(f'Epoch [{epoch + 1}/{num_epochs}], Training Loss: {epoch_loss}')
    wandb.log({
        "epoch": epoch + 1,
        "training_loss": epoch_loss
    })
    
    # Track on the small subset
    model.eval()
    epoch_embeddings = []
    with torch.no_grad():
        for i, (input_ids, attention_mask, labels) in tqdm(enumerate(tracking_dataloader), desc="Generating embeddings", leave=False):
            input_ids, attention_mask, labels = input_ids.to(device), attention_mask.to(device), labels.to(device)
            outputs = model(input_ids, attention_mask=attention_mask, output_hidden_states=True)
            hidden_states = outputs.hidden_states
            embeddings = pad_embeddings(hidden_states[-2][:,-2,:].cpu().numpy())
            epoch_embeddings.append(embeddings)
    
    embeddings_list.append(epoch_embeddings)

    if len(embeddings_list) > 3:
        embeddings_list.pop(0)
    
    # Every 'epoch_threshold' epochs, compute new sensitive neurons and reset dropout period
    if (epoch + 1) % epoch_threshold == 0:
        current_sensitive_neurons = get_sensitive_neurons(embeddings_list[-epoch_threshold:])
        dropout_applied_epochs = 0  # Reset the dropout period to start at the next epoch
        wandb.log({
            "epoch": epoch + 1,
            "num_sensitive_neurons": len(current_sensitive_neurons)
        })
        
    # Compute eigenscore
    embeddings = get_embeddings(model, tokenizer=tokenizer, test_texts=test_texts)
    average_eigenscore = compute_efficient_eigenscore(embeddings)

    wandb.log({"average_eigenscore": average_eigenscore})

wandb.finish()
print("Training complete!")