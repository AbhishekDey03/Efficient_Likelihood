# Efficient Likelihood Calculations
The work in this repo documents the creation, implementation and efficient calculations using the Point-Spread function of the VLA-FIRST detector.

The repo contains the jupyter notebook (and [PDF from it](Factoring%20the%20Covariance%20Matrix.pdf)) that created the matrix. This notebook covers the statistical assumptions and the numerical algorithm used to create the covariance, before scaling with $\sigma_\mathrm{rms}$, and discussion of SVD and Cholesky decomposition.

The second notebook covers methods that can be used to factor the covariance matrix, discussing the assumptions and reasons why we take a black  [diagonal](Create%20the%20Covariance%20Matrix.pdf) assumption.

`main_autoencoder_optioni` are very similar python scripts, just with options for different likelihood efficiency methods toggled on or off. This is done so that they can run all at the same time without interference.

The results are found in:
https://wandb.ai/deya-03-the-university-of-manchester/Efficient_Likelihood/reports/Efficient-Likelihood-for-VLA-FIRST-Statistical-AE--VmlldzoxMjg0MTYzMA

The work was done in collaboration with Ahmad Abdelhakam Mahmoud, who posts his code on using this covariance matrix to preserve statistical distributions here:
https://github.com/Eazo1/Neural_Compression_Masters
