"""Look-elsewhere correction for GAMBIT EWMSSM analysis"""

from analysis import collider_analyses_from_long_YAML, JMCJoint, deep_merge, LEEcorrection, LEECorrectorAnalysis, LEECorrectorMaster
import tensorflow as tf
from tensorflow_probability import distributions as tfd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import h5py

N = int(1e4)
do_mu_tests=True
do_gof_tests=True

print("Starting...")

# Load analysis metadata from YAML and construct helper ColliderAnalysis objects for it
f = 'old_junk/CBit_analyses.yaml'
stream = open(f, 'r')
analyses_read, SR_map, iSR_map = collider_analyses_from_long_YAML(stream,replace_SR_names=True)

# Full published MSSMEW dataset, from which we will extract signal predictions
hdf5file = "/home/farmer/repos/jpvc_analyses/data/MSSMEW_pp_final.hdf5"

gname = "MSSMEW"
f = h5py.File(hdf5file,'r')
g = f[gname]

# Signal predictions in hdf5 file have dataset names like this: 
#LHC_signals\ @ColliderBit::calc_LHC_signals::ATLAS_13TeV_3b_24invfb__meff340_ETmiss70__i14__signal
#LHC_signals\ @ColliderBit::calc_LHC_signals::ATLAS_13TeV_3b_24invfb__meff340_ETmiss70__i14__signal_uncert

# Selection of LHC analyses to use in this calculation
TeV13 = [
"ATLAS_13TeV_4LEP_36invfb",
"ATLAS_13TeV_RJ3L_2Lep2Jets_36invfb",
"ATLAS_13TeV_RJ3L_3Lep_36invfb",
"ATLAS_13TeV_3b_24invfb",
"ATLAS_13TeV_MultiLEP_2Lep0Jets_36invfb",
"ATLAS_13TeV_MultiLEP_2LepPlusJets_36invfb",
"ATLAS_13TeV_MultiLEP_3Lep_36invfb",
"CMS_13TeV_1LEPbb_36invfb",
"CMS_13TeV_MultiLEP_2SSLep_36invfb",
"CMS_13TeV_MultiLEP_3Lep_36invfb",
"CMS_13TeV_2LEPsoft_36invfb",
"CMS_13TeV_2OSLEP_36invfb"
]

# Selection of 13 TeV analyses using aggregate "discovery" signal regions instead of all of them 
TeV13_disc = [
"ATLAS_13TeV_4LEP_36invfb",
"ATLAS_13TeV_RJ3L_2Lep2Jets_36invfb",
"ATLAS_13TeV_RJ3L_3Lep_36invfb",
"ATLAS_13TeV_3b_discoverySR_24invfb",
"ATLAS_13TeV_MultiLEP_2Lep0Jets_36invfb",
"ATLAS_13TeV_MultiLEP_2LepPlusJets_36invfb",
"ATLAS_13TeV_MultiLEP_3Lep_36invfb",
"CMS_13TeV_1LEPbb_36invfb",
"CMS_13TeV_MultiLEP_2SSLep_36invfb",
"CMS_13TeV_MultiLEP_3Lep_36invfb",
"CMS_13TeV_2LEPsoft_36invfb",
"CMS_13TeV_2OSLEP_36invfb"
]
        
# 8TeV analyses
TeV8 = [
"CMS_8TeV_MultiLEP_3Lep_20invfb",
"CMS_8TeV_MultiLEP_4Lep_20invfb",
"ATLAS_8TeV_1LEPbb_20invfb",
"ATLAS_8TeV_2LEPEW_20invfb",
"ATLAS_8TeV_3LEPEW_20invfb"
]

#LHC_analyses = TeV13 #+ TeV8
#LHC_analyses = TeV13_disc
LHC_analyses = TeV13

hdf5_names = {} # Names of hdf5 datasets to load
analyses = {}
for name,a in analyses_read.items():
  if name in LHC_analyses:
    hdf5_names[name] = ["#LHC_signals @ColliderBit::calc_LHC_signals::{0}__{1}__signal".format(name,SR_map[SR]) for SR in a.SR_names]
    analyses[name] = a

class SigGen:
    """Object to supply signal hypotheses in chunks"""
    def __init__(self,analyses,h5group,thin=None,batch_size=100):
        self.count = len(h5group['LogLike'][:])
        if thin is not None:
            # Randomly choose size/thin signals from the set. Mostly for speeding up tests.
            # Needs to be consistent across all events, so need to choose in advance and generate
            # all required events at once if thinning.
            self.thin_indices = np.arange(0,self.count)
            np.random.shuffle(self.thin_indices)
            self.count = self.count // thin

        self.thin = thin
           
        print("{0} signal hypotheses to consider".format(self.count))
        self.analyses = analyses
        self.chunk_size = batch_size
        self.h5group = h5group # location of datasets in hdf5 file
        self.j = 0

        # Verify that all required mappings exist
        for name,a in self.analyses.items():
            for SR,dsetname in zip(a.SR_names,hdf5_names[name]):
                SR_map[SR]
                pass

    def reset(self): 
        self.j = 0

    def next(self):
        j = self.j
        size = self.chunk_size

        # Build validity map in this chunk
        mvalid = None
        for name,a in self.analyses.items():
            for SR,dsetname in zip(a.SR_names,hdf5_names[name]):
                if self.thin is not None:
                    # I think faster to read all signals and then select the thinned ones in memory
                    valid = np.array(self.h5group[dsetname+"_isvalid"][:][self.thin_indices[j:j+size]],dtype=np.bool) 
                else:
                    valid = np.array(self.h5group[dsetname+"_isvalid"][j:j+size],dtype=np.bool)
                if mvalid is None: mvalid = valid
                else: mvalid = mvalid & valid
        size = len(mvalid) # in case of reaching end of signal datasets
 
        if self.thin is not None:
            sigIDs = list(self.thin_indices[j:j+size][mvalid])
        else:
            sigIDs = list(np.arange(j,j+size)[mvalid])

        #print("mvalid:",mvalid)
        # Extract signals
        signal_chunk = {}
        for name,a in self.analyses.items():
            signal_chunk[name] = {}
            datalist = []
            for SR,dsetname in zip(a.SR_names,hdf5_names[name]):
                if self.thin is not None:
                    # I think faster to read all signals and then select the thinned ones in memory
                    datalist += [tf.constant(self.h5group[dsetname][:][self.thin_indices[j:j+size]][mvalid],dtype=c.TFdtype)] 
                else:
                    datalist += [tf.constant(self.h5group[dsetname][j:j+size][mvalid],dtype=c.TFdtype)]
            # Merge all predictions for this analysis under 's' parameter
            stacked = tf.stack(datalist,axis=1)
            signal_chunk[name]['s'] = stacked

        self.j += size
        #print("signal_chunk:", signal_chunk)
        #print("sigIDs:", len(sigIDs))
        return signal_chunk, sigIDs 

nosignal = {a.name: {'s': tf.constant([[0. for sr in a.SR_names]],dtype=c.TFdtype)} for a in analyses.values()}
DOF = 4 # There were 4 parameters in the EWMSSM scan. So if asymptotic theory is in good shape then results should be distributed as chi^2 with DOF=4. But it probably won't be.

# Specific signal whose local distributions and p-values we would like to know

path = 'TEST_EWMSSM'
master_name = 'all'
nullname = 'background'
lee = LEECorrectorMaster(analyses,path,master_name,nosignal,nullname)
#lee.ensure_equal_events()
lee.add_events(int(1e3))
lee.process_null()
lee.process_local_signal()
lee.process_signals(SigGen(analyses,g,thin=None,batch_size=int(5e5)),new_events_only=True,event_batch_size=10,dbtype='hdf5')
df = lee.load_results(lee.combined_table,['neg2logL_null','neg2logL_profiled_quad'])

#print('neg2logL_null:',df['neg2logL_null'])
#print('neg2logL_profiled_quad:',df['neg2logL_profiled_quad'])
# Take the minimum over the null and profiled neg2logLs, to ensure that the null is treated
# as nested within the alternate. Needed when sampling of alternate isn't good enough that
# null is 'naturally' nested.
min_neg2logL = df[['neg2logL_null','neg2logL_profiled_quad']].min(axis=1)
chi2_quad = df['neg2logL_null'] - min_neg2logL

# Get bootstrap resampling to improve statistics
bootstrap_neg2logL, bootstrap_b_neg2logL = lee.get_bootstrap_sample(1000,batch_size=100,dbtype='hdf5')
#bootstrap_neg2logL, bootstrap_b_neg2logL = lee.get_bootstrap_sample(1000,batch_size=100,dbtype='sqlite')
#bootstrap_neg2logL, bootstrap_b_neg2logL = lee.get_bootstrap_sample('all',batch_size=100)
bootstrap_chi2 = bootstrap_b_neg2logL - np.min(np.stack([bootstrap_b_neg2logL,bootstrap_neg2logL],axis=1),axis=1) # Again make sure null is nested
print("bootstrap_chi2:", bootstrap_chi2.shape)

# Compute observed value of test statistic

# Plots!
fig  = plt.figure(figsize=(12,4))
ax1 = fig.add_subplot(1,2,1)
ax2 = fig.add_subplot(1,2,2)
ax2.set(yscale="log")
sns.distplot(chi2_quad, color='m', kde=False, ax=ax1, norm_hist=True, label="LEEC quad")
sns.distplot(chi2_quad, color='m', kde=False, ax=ax2, norm_hist=True, label="LEEC quad")
sns.distplot(bootstrap_chi2, color='b', kde=False, ax=ax1, norm_hist=True, label="LEEC bootstrap")
sns.distplot(bootstrap_chi2, color='b', kde=False, ax=ax2, norm_hist=True, label="LEEC bootstrap")

qx = np.linspace(0, np.max(chi2_quad),1000) # 6 sigma too far for tf, cdf is 1. single-precision float I guess
qy = 0.5*tf.math.exp(tfd.Chi2(df=DOF).log_prob(qx)) # Half-chi2 since negative signal contributions not possible?
sns.lineplot(qx,qy,color='g',ax=ax1, label="asymptotic")
sns.lineplot(qx,qy,color='g',ax=ax2, label="asymptotic")

ax1.legend(loc=1, frameon=False, framealpha=0, prop={'size':10}, ncol=1)
ax2.legend(loc=1, frameon=False, framealpha=0, prop={'size':10}, ncol=1)
   
fig.tight_layout()
fig.savefig("{0}/LEEC_quad_{1}_{2}.png".format(path,master_name,nullname))
