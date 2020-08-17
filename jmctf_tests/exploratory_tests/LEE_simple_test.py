"""Simple test setup for LEE correction driver object 
"""

import numpy as np
import matplotlib.pyplot as plt
from jmctf import NormalAnalysis, BinnedAnalysis, JointDistribution, plotting

# Create component analysis objects
x_obs = 5.
sigma = 2.
norm = NormalAnalysis("Test normal", x_obs, sigma)
analyses = [norm]

# For this test, the "mu" parameter for NormalAnalysis will be the
# one we use to create our set of alternate hypothesis

N = 100 # Number of alternate hypotheses to generate (controls density of hypotheses)
null_mu = 0
mu = np.linspace(null_mu - 5*sigma, null_mu + 5*sigma, N)

class SigGen:
    """Object to supply signal hypotheses in chunks
       Replace with something that e.g. reads from HDF5 file
       in real cases. Needs to be usable as an generator."""
    def __init__(self,N,alt_hyp):
        self.count = N
        self.chunk_size = 10
        self.alt_hyp = alt_hyp
        self.j = 0

    def __iter__(self):
        while True:
            j = self.j
            size = self.chunk_size
            chunk = {name: {par: dat[j:j+size] for par,dat in a.items()} for name,a in self.alt_hyp.items()}
            this_chunk_size = c.deep_size(chunk)
            if this_chunk_size==0: # Or some other error?
                break # Finished
            ids = list(range(j,j+this_chunk_size))
            self.j += this_chunk_size
            yield chunk, ids

import tensorflow as tf
import jmctf.common as c
null_hyp = {"Test normal": {"mu": 0}}
alt_hyp = {"Test normal": {"mu": mu}} 
DOF = 1

# Hypothesis generator function for use with LEE in tests
# We actually need to provide a function that *creates* the generator since we need to run it multiple times.
# Replace with something that e.g. reads from HDF5 file in real cases.
def get_hyp_gen():
    return SigGen(N,alt_hyp)

from jmctf.LEE import LEECorrectorMaster

path = 'Test_normal'
master_name = 'test_1'
nullname = 'mu=0'
lee = LEECorrectorMaster(analyses,path,master_name,null_hyp,nullname)

# Make sure we are providing all the required analysis parameters
free, fixed, nuis = lee.decomposed_parameter_shapes()
print("free:", free)
print("fixed:", fixed)
print("nuis:", nuis)
print("null_hyp:", null_hyp)
print("alt_hyp:", alt_hyp)

# Generate pseudodata samples from null hypothesis
lee.add_events(1e3)

# Fit null hypothesis nuisance parameters to recorded samples
lee.process_null()

# Fit all alternate hypothesis nuisance parameters to recorded
# samples, recording results only for the one which is the best 
# fit for each sample (not feasible, or necessary, to record them all)
lee.process_alternate(get_hyp_gen,new_events_only=True)

# Load up results from the output databases (as pandas dataframes)
df_null, df_null_obs = lee.load_results(lee.null_table,['neg2logL'],get_observed=True)
df_prof, df_prof_obs = lee.load_results(lee.profiled_table,['neg2logL_quad','logw'],get_observed=True)

# Compute likelihood ratios
LLR = df_null['neg2logL'] - df_prof['neg2logL_quad']

# Plot TS distribution 
fig = plt.figure(figsize=(5,3))
ax = fig.add_subplot(111)
plotting.plot_chi2(ax,LLR,DOF) 
ax.legend(loc=1, frameon=False, framealpha=0, prop={'size':10}, ncol=1)
fig.tight_layout()
fig.savefig("LEE_simple_test_plot_LLR.svg")
