import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
import matplotlib.pyplot as plt
import wandb
from torch.distributions import Normal, MultivariateNormal
# --- Model Components ---
from encoder import Encoder
from decoder import Decoder
import plotting_functions

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

option = 4
# option 1: Identity matrix, option 2: 1/9 of the matrix, option 3: Full matrix, option 4: Block diag

import astropy.units as u

def block_diagonal_mvg_NLL(cov, x, mu, n, batch_size=1):
    # x is of shape (batch_size, D) where D = total pixels
    # cov is the full covariance matrix from which we take the top-left block for every block.
    batch_size, D = x.shape
    num_blocks = D // n
    remainder_size = D % n
    z = x - mu  # shape: (batch_size, D)

    # Process main blocks using the top-left block slice from cov
    if num_blocks > 0:
        # Always use the same top-left block
        cov_main = cov[:n, :n]  # shape: (n, n)
        L_main = torch.linalg.cholesky(cov_main)  # shape: (n, n)
        # Expand L_main to apply to every block in every batch:
        # New shape: (batch_size, num_blocks, n, n)
        L_main_batch = L_main.unsqueeze(0).unsqueeze(0).expand(batch_size, num_blocks, n, n)
        # Extract the main blocks from z: shape (batch_size, num_blocks, n)
        z_main = z[:, :num_blocks * n].reshape(batch_size, num_blocks, n)
        # Solve for y in L_main * y = z_main (batched triangular solve)
        y_main = torch.linalg.solve_triangular(L_main_batch, z_main.unsqueeze(-1), upper=False).squeeze(-1)
        # Sum the squared solutions
        mahalanobis_main = (y_main ** 2).sum()
        # The log determinant from the top-left block, repeated for each block
        logdet_main = num_blocks * (2 * torch.sum(torch.log(torch.diag(L_main))))
    else:
        mahalanobis_main = 0
        logdet_main = 0

    # Process the remainder block (if it exists) using its corresponding top-left slice
    if remainder_size > 0:
        cov_rem = cov[:remainder_size, :remainder_size]
        L_rem = torch.linalg.cholesky(cov_rem)
        z_rem = z[:, num_blocks * n:]
        # Expand L_rem for the batch dimension: (batch_size, remainder_size, remainder_size)
        L_rem_batch = L_rem.unsqueeze(0).expand(batch_size, remainder_size, remainder_size)
        y_rem = torch.linalg.solve_triangular(L_rem_batch, z_rem.unsqueeze(-1), upper=False).squeeze(-1)
        mahalanobis_rem = (y_rem ** 2).sum()
        logdet_rem = 2 * torch.sum(torch.log(torch.diag(L_rem)))
    else:
        mahalanobis_rem = 0
        logdet_rem = 0

    total_logdet = logdet_main + logdet_rem
    total_mahalanobis = mahalanobis_main + mahalanobis_rem

    # Final negative log-likelihood calculation
    nll = 0.5 * (total_logdet + total_mahalanobis + D * batch_size * torch.log(torch.tensor(2) * torch.pi))
    return nll




class MemoryMappedDataset(Dataset):
    def __init__(self, mmap_data, device):
        self.data = mmap_data
        self.device = device

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        # Returns a tensor in the shape stored in the npy file.
        return torch.tensor(self.data[idx], dtype=torch.float32)
    
def find_covariance_matrix(image_size, sigma,rms_noise=0.15):
    """
    Create a pixel-to-pixel correlation matrix for a square image.
    Inputs:
      - image_size: height/width of the image
      - sigma: standard deviation used in the Gaussian correlation.
    """
    x, y = np.meshgrid(np.arange(image_size), np.arange(image_size), indexing="ij")
    pixel_coords = np.stack((x.ravel(), y.ravel()), axis=1)
    i, j = pixel_coords[:, 0], pixel_coords[:, 1]

    di = i[:, None] - i[None, :]  # Difference in x for all pairs
    dj = j[:, None] - j[None, :]  # Difference in y for all pairs
    d = 1.8 * np.sqrt(di**2 + dj**2)  # Scaled Euclidean distances

    C = (1 / np.sqrt(2 * np.pi * sigma**2)) * np.exp(-d**2 / (2 * sigma**2))
    np.fill_diagonal(C, 1)  # Set diagonal to 1
    return C*rms_noise**2



# Data loading paths
train_data_path = '/share/nas2_3/amahmoud/week5/galaxy_out/train_data.npy'
valid_data_path = '/share/nas2_3/amahmoud/week5/galaxy_out/valid_data_original.npy'

train_data_mmap = np.load(train_data_path, mmap_mode='r')
valid_data_mmap = np.load(valid_data_path, mmap_mode='r')

# Create datasets and loaders
train_dataset = MemoryMappedDataset(train_data_mmap, device=None)
valid_dataset = MemoryMappedDataset(valid_data_mmap, device=None)

train_loader = DataLoader(train_dataset, batch_size=4, shuffle=True)
valid_loader = DataLoader(valid_dataset, batch_size=4, shuffle=False)

# Autoencoder class
class Autoencoder(nn.Module):
    def __init__(self, num_hiddens, num_residual_layers, num_residual_hiddens):
        super(Autoencoder, self).__init__()
        self.encoder = Encoder(num_hiddens, num_residual_layers, num_residual_hiddens)
        self.decoder = Decoder(num_hiddens, num_residual_layers, num_residual_hiddens, input_dim=num_hiddens)
    

    def forward(self, x):
        z = self.encoder(x)
        x_recon = self.decoder(z)
        return x_recon

# Setup parameters
num_hiddens = 256
num_residual_layers = 2
num_residual_hiddens = 32
learning_rate = 2e-4
num_training_updates = 1000
fwhm = 5.4 
image_size = 150
# Block diagonal size
n=12

wandb.init(
    project="Efficient_Likelihood",
    config={
        "architecture": "AE",
        "covariance_method":"block diagonal 12x12",
    },
    reinit=True,
)

# Instantiate AE and optimizer
autoencoder = Autoencoder(num_hiddens, num_residual_layers, num_residual_hiddens).to(device)
optimizer = optim.Adam(autoencoder.parameters(), lr=learning_rate)

# Training loop
train_losses = []
iteration = 1
autoencoder.train()

correlation_matrix = find_covariance_matrix(image_size, fwhm) #circular point spread
cov = torch.tensor(correlation_matrix, dtype=torch.float32, device=device)
print("Starting training...")
while iteration < num_training_updates:
    for images in train_loader:
        
        # If the tensor has 5 dimensions (e.g., [batch, 1, 1, H, W]), remove the extra dimension.
        if images.dim() == 5:
            images = images.squeeze(2)  # Remove the extra dimension at index 2.
        # If images come in as 3D (i.e., missing the channel dimension), add one.
        elif images.dim() == 3:
            images = images.unsqueeze(1)
        
        images = images.to(device)

        optimizer.zero_grad()
        recon = autoencoder(images)
        #loss = F.mse_loss(recon, images, reduction='sum')

        images_flat = images.view(images.size(0), -1)
        recon_flat = recon.view(images.size(0), -1)


        # --- Identity  matrix calculation ---
        if option == 1:
            total_pixels = images_flat.size(1)
            identity = torch.eye(total_pixels, device=images.device)  # Create identity matrix
            mvn = MultivariateNormal(loc=recon_flat, scale_tril=identity)
            loss = -mvn.log_prob(images_flat).sum()
            

            D_total = images_flat.size(0) * total_pixels  
            # Mean Reduction
            loss_mean = loss/D_total

            

        # --- 1/9 of the matrix calculation ---
        elif option == 2:
            batch_size, total_pixels = images_flat.size()
            
            # Use precomputed indices to subset data efficiently
            images_flat_subset = images_flat[:, subset_indices]
            recon_flat_subset = recon_flat[:, subset_indices]

            # Use precomputed Cholesky factor for covariance matrix
            mvn = MultivariateNormal(loc=recon_flat_subset, scale_tril=scale_tril_subset)
            loss = -mvn.log_prob(images_flat_subset).sum()

            # Normalization
            D_total = images_flat_subset.size(0) * images_flat_subset.size(1)
            loss_mean = loss / D_total

        # --- Full matrix calculation ---
        elif option == 3:
            mvn = MultivariateNormal(loc=recon_flat, scale_tril=scale_tril)
            loss = -mvn.log_prob(images_flat).sum()
            # Malahanobis distance
            D_total = images_flat.size(0) * images_flat.size(1)  
            # Mean Reduction.
            loss_mean = loss/D_total

        elif option == 4:
            # Block diagonal calculation
            loss = block_diagonal_mvg_NLL(cov, images_flat, recon_flat, n, batch_size=images_flat.size(0))
            D_total = images_flat.size(0) * images_flat.size(1)  
            loss_mean = loss / D_total

        else:
            print("Invalid option. Please choose 1, 2, 3, or 4.")
            break
        bits_per_dim = loss / (images.size(0) * images.size(2) * images.size(3)*np.log(2))  # Divide by log(2) to convert to bits per dim.


        loss.backward()
        optimizer.step()

        train_losses.append(loss.item())
        wandb.log({"train/loss": loss.item(),
                   "train/bits_per_dim": bits_per_dim.item(),
                   "train/mean_loss": loss_mean.item()
                   })
        

        if iteration % 100 == 0:
            print(f"Iteration {iteration}, training loss: {loss.item():.4f}")

        if iteration % 10 ==0:
             # Validation loop
            autoencoder.eval()
            with torch.no_grad():
                total_val_loss = 0.0
                num_batches = 0
                bits_per_dim_val = 0.0
                # mean reduction on the loss is a bit different here, we will log full dimensionss by iteratively incrementing:
                total_pixels_val = 0.0
                for val_images in valid_loader:
                    # Apply the same dimension fix as above.
                    if val_images.dim() == 5:
                        val_images = val_images.squeeze(2)
                    elif val_images.dim() == 3:
                        val_images = val_images.unsqueeze(1)
                    val_images = val_images.to(device)
                    recon_val = autoencoder(val_images)
                    val_images_flat = val_images.view(val_images.size(0), -1)
                    recon_val_flat = recon_val.view(val_images.size(0), -1)
                    
                    # --- Identity Matrix Calculation ---
                    if option==1:
                        total_pixels = val_images_flat.size(1)
                        identity = torch.eye(total_pixels, device=val_images.device)
                        mvn = MultivariateNormal(loc=recon_val_flat, scale_tril=identity)
                        loss_val = -mvn.log_prob(val_images_flat).sum()
                        
                        # Malahanobis Distance
                        D_total_val = val_images_flat.size(0) * total_pixels
                        mahalanobis_distance_val = 2 * loss_val - 0 - D_total_val * np.log(2 * np.pi)


                    # --- 1/9 of the matrix calculation ---
                    if option==2:
                        val_images_flat = val_images.view(val_images.size(0), -1)
                        recon_val_flat = recon_val.view(val_images.size(0), -1)
                        total_pixels = val_images_flat.size(1)
                        subset_size = total_pixels // 9  # 1/9 of the pixels
                        subset_indices = torch.randperm(total_pixels)[:subset_size]
                        val_images_flat_subset = val_images_flat[:, subset_indices]
                        recon_val_flat_subset = recon_val_flat[:, subset_indices]
                        scale_tril_subset = scale_tril[subset_indices][:, subset_indices]
                        mvn = MultivariateNormal(loc=recon_val_flat_subset, scale_tril=scale_tril_subset)
                        loss_val = -mvn.log_prob(val_images_flat_subset).sum()
                        

                        # Malahanobis Distance
                        D_total_val = val_images_flat_subset.size(0) * val_images_flat_subset.size(1)
                        log_det_val = 2 * torch.sum(torch.log(torch.diag(scale_tril_subset)))
                        mahalanobis_distance_val = 2 * loss_val - val_images_flat_subset.size(0) * log_det_val - D_total_val * np.log(2 * np.pi)


                    # --- Full Covariance Matrix Calculation ---
                    if option==3:
                        mvn = MultivariateNormal(loc=recon_val_flat, scale_tril=scale_tril)
                        loss_val = -mvn.log_prob(val_images_flat).sum

                        # Malahanobis Distance
                        D_total_val = val_images_flat.size(0) * val_images_flat.size(1)
                        log_det = 2 * torch.sum(torch.log(torch.diag(scale_tril)))
                        mahalanobis_distance_val = 2 * loss_val - val_images_flat.size(0) * log_det - D_total_val * np.log(2 * np.pi)
                    
                    if option==4:
                        loss_val = block_diagonal_mvg_NLL(cov, val_images_flat, recon_val_flat, n, batch_size=val_images_flat.size(0))
                        D_total = val_images_flat.size(0) * val_images_flat.size(1)  
                        loss_mean = loss_val / D_total


                    bits_per_dim_val += loss_val / (val_images.size(0) * val_images.size(2) * val_images.size(3) * np.log(2))

                    total_val_loss += loss_val.item()
                    total_pixels_val += val_images.size(0) * val_images.size(2) * val_images.size(3) #batch size * image size * image size
                    num_batches += 1
                    
                avg_val_loss = total_val_loss / num_batches if num_batches > 0 else 0.0
                wandb.log({"validation/loss": avg_val_loss,
                "validation/bits_per_dim": bits_per_dim_val,
                "validation/loss_mean": total_val_loss /total_pixels_val
                })

                print(f"Validation loss: {avg_val_loss:.4f}")
            autoencoder.train()

        iteration += 1
        if iteration >= num_training_updates:
            break
          
   

# Evaluation (for visualization)
autoencoder.eval()
with torch.no_grad():
    for images in valid_loader:
        # Again, fix the shape if needed.
        if images.dim() == 5:
            images = images.squeeze(2)
        elif images.dim() == 3:
            images = images.unsqueeze(1)
        images = images.to(device)
        recon_images = autoencoder(images)
        break

# Use your previously defined plotting functions
plotting_functions.display_images(images, recon_images, num_images=8, step=iteration)

# Save the model
save_directory = '/share/nas2_3/adey/astro/outputs_sem_2/'
model_save_path = os.path.join(save_directory, 'autoencoder_model.pth')
torch.save(autoencoder.state_dict(), model_save_path)
print("Model saved to", model_save_path)

wandb.finish()
