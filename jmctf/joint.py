"""Classes and functions associated with creating and fitting joint distribution objects"""

import numpy as np
import tensorflow as tf
from tensorflow_probability import distributions as tfd
from collections.abc import Mapping
import massminimize as mm
from . import common as c

#tmp
id_only = False

def neg2LogL(pars,const_pars,analyses,data,transform=None):
    """General -2logL function to optimise
       TODO: parameter 'transform' feature not currently in use, probably doesn't work correctly
    """
    #print("In neg2LogL:")
    #print("pars:", c.print_with_id(pars,id_only))
    #print("const_pars:", c.print_with_id(const_pars,id_only))
    if transform is not None:
        pars_t = transform(pars)
    else:
        pars_t = pars
    if const_pars is None:
        all_pars = pars_t
    else:
        all_pars = c.deep_merge(const_pars,pars_t)

    # Sanity check: make sure parameters haven't become nan somehow
    anynan = False
    nanpar = ""
    for a,par_dict in pars.items():
        for p, val in par_dict.items():
            if tf.math.reduce_any(tf.math.is_nan(val)):
                anynan = True
                nanpar += "\n    {0}::{1}".format(a,p)
    if anynan:
        msg = "NaNs detected in parameter arrays during optimization! The fit may have become unstable and wandering into an invalid region of parameter space; please check your analysis setup. Parameter arrays containing NaNs were:{0}".format(nanpar)
        raise ValueError(msg)

    # Parameters will enter this function pre-scaled such that MLEs have variance ~1
    # So we need to set the pre-scaled flag for the JointDistribution constructor to
    # avoid applying the scaling a second time.
    joint = JointDistribution(analyses.values(),all_pars,pre_scaled_pars=True)
    q = -2*joint.log_prob(data)
    #print("q:", q)
    #print("all_pars:", all_pars)
    #print("logL parts:", joint.log_prob_parts(data))

    if tf.math.reduce_any(tf.math.is_nan(q)):
        # Attempt to locate components generating the nans
        component_logprobs = joint.log_prob_parts(data)
        nan_components = ""
        for comp,val in component_logprobs.items():
            if tf.math.reduce_any(tf.math.is_nan(val)):
                nan_components += "\n    {0}".format(comp)                
        msg = "NaNs detect in result of neg2LogL calculation! Please check that your input parameters are valid for the distributions you are investigating, and that the fit is stable! Components of the joint distribution whose log_prob contained nans were:" + nan_components
        raise ValueError(msg)
    total_loss = tf.math.reduce_sum(q)
    return total_loss, q, None, None

def optimize(pars,const_pars,analyses,data,transform=None,log_tag='',verbose=False):
    """Wrapper for optimizer step that skips it if the initial guesses are known
       to be exact MLEs"""
    opts = {"optimizer": "Adam",
            "step": 0.05,
            "tol": 0.01,
            "grad_tol": 1e-4,
            "max_it": 100,
            "max_same": 5,
            "log_tag": log_tag,
            "verbose": verbose 
            }

    kwargs = {'analyses': analyses,
              'data': data,
              'transform': transform
              }
    #print("In 'optimize'")
    #print("pars:", c.print_with_id(pars,id_only))
    #print("const_pars:", c.print_with_id(const_pars,id_only))

    # Convert free parameter initial guesses into TensorFlow Variable objects
    free_pars = c.convert_to_TF_variables(pars) 

    #print("Converted free parameters into TensorFlow Variables:")
    #print("free_pars:", free_pars)
    #print("free_pars (id):", c.print_with_id(free_pars,id_only))

    # Sanity check input parameters
    anynan = False
    nanpar = ""
    for pars_tf in [pars, const_pars]:
        for a,par_dict in pars_tf.items():
            for p, val in par_dict.items():
                if tf.math.reduce_any(tf.math.is_nan(val)):
                    anynan = True
                    nanpar += "\n    {0}::{1}".format(a,p)
    if anynan:
        msg = "NaNs detected in input parameter arrays for 'optimize' function! Parameter arrays containing NaNs were:{0}".format(nanpar)
        raise ValueError(msg)

    all_exact_MLEs = True
    for a in analyses.values():
        if not a.exact_MLEs: all_exact_MLEs = False

    if all_exact_MLEs:
        if verbose: print("All starting MLE guesses are exact: skipping optimisation") 
        total_loss, q, none, none = neg2LogL(free_pars,const_pars,**kwargs)
    else:
        # For analyses that have exact MLEs, we want to move those parameters from the
        # "free" category into the "fixed" category.
        reduced_free_pars = {}
        enlarged_const_pars = {}
        for a in analyses.values():
            a_free_pars  = free_pars.get(a.name,{})
            a_const_pars = const_pars.get(a.name,{})
            if a.exact_MLEs:
                # Move free parameters for this analysis into "const" category
                if verbose: print("Starting MLE guesses are exact for analysis '{0}', removing these parameters from optimisation step.".format(a.name))
                enlarged_const_pars[a.name] = c.deep_merge(a_free_pars,a_const_pars)
            else:
                # Keep free parameters for this analysis in the "free" category
                reduced_free_pars[a.name] = a_free_pars
                enlarged_const_pars[a.name] = a_const_pars

        if verbose: print("Beginning optimisation")
        #f = tf.function(mm.tools.func_partial(neg2LogL,**kwargs))
        kwargs["const_pars"] = enlarged_const_pars
        f = mm.tools.func_partial(neg2LogL,**kwargs)
        #print("About to enter optimizer")
        #print("pars:", c.print_with_id(reduced_free_pars,False))
        q, none, none = mm.optimize(reduced_free_pars, f, **opts)

    # Rebuild distribution object with fitted parameters for output to user
    if transform is not None:
        pars_t = transform(free_pars)
    else:
        pars_t = free_pars
    if const_pars is None:
        all_pars = pars_t
    else:
        all_pars = c.deep_merge(const_pars,pars_t)
    joint = JointDistribution(analyses.values(),all_pars,pre_scaled_pars=True)

    # Output is:
    #  JointDistribution with parameters set to the MLEs,
    #  -2*log_prob(samples) of the JointDistribution under the MLEs for all samples
    #  Full parameter dictionary used to construct the fitted JointDistribution
    #  parameter dictionary containing only the fitted free parameters
    #  parameter dictionary containing only the fixed ("bystander") parameters
    return joint, q, all_pars, pars_t, const_pars

class JointDistribution(tfd.JointDistributionNamed):
    """Object to combine analyses together and treat them as a single
       joint distribution. Uses JointDistributionNamed for most of the
       underlying work.

       TODO: This object has a bunch of stuff that only works with BinnedAnalysis
             objects as the 'analyses'. Needs to be generalised. 
    """
   
    def __init__(self, analyses, pars=None, pre_scaled_pars=False):
        """ 
        :param analyses: list of analysis-like objects to be combined
        :type analyses: list
        :param pars: dictionary of parameters for all analysis objects, to fix
                parameters for sampling (default: None)
        :type pars: dictionary, optional
        :param pre_scaled_pars: If True, all input parameters are already scaled 
                such that MLEs have variance of approx. 1 (for more stable fitting).
                If False, all parameters are conventionally (i.e. not) scaled, and
                require scaling internally.
        :type pre_scaled_pars: bool, optional
        """
        #print("In JointDistribution constructor (pre_scaled_pars={0})".format(pre_scaled_pars))
         
        self.analyses = {a.name: a for a in analyses}
        self.Osamples = {}
        for a in self.analyses.values():
           self.Osamples.update(c.add_prefix(a.name,a.get_observed_samples()))
        if pars is not None:
            # Convert parameters to TensorFlow constants, if not already TensorFlow objects
            #print("pars:", c.print_with_id(pars,id_only))
            pars_tf = c.convert_to_TF_constants(pars,ignore_variables=True)
            #print("pars_tf:", c.print_with_id(pars_tf,id_only))
            # Check that parameters are not NaN
            anynan = False
            nanpar = ""
            for a,par_dict in pars_tf.items():
                for p, val in par_dict.items():
                    if tf.math.reduce_any(tf.math.is_nan(val)):
                        anynan = True
                        nanpar += "\n    {0}::{1}".format(a,p)
            if anynan:
                msg = "NaNs detected in input parameter arrays for JointDistribution! Parameter arrays containing NaNs were:{0}".format(nanpar)
                raise ValueError(msg)
            self.pars = self.prepare_pars(pars_tf,pre_scaled_pars)
            #print("self.pars:", c.print_with_id(self.pars,id_only))
            dists = {} 
            self.Asamples = {}
            for a in self.analyses.values():
                d = c.add_prefix(a.name,a.tensorflow_model(self.pars[a.name]))
                dists.update(d)
                self.Asamples.update(c.add_prefix(a.name,a.get_Asimov_samples(self.pars[a.name])))
            super().__init__(dists) # Doesn't like it if I use self.dists, maybe some construction order issue...
            self.dists = dists
        else:
            self.pars = None
        # If no pars provided can still fit the analyses, but obvious cannot sample or compute log_prob etc.
        # TODO: can we fail more gracefully if people try to do this?
        #       Or possibly the fitting stuff should be in a different object? It seems kind of nice here though.
        #print("self.pars = ", self.pars)

    def fix_parameters(self, pars):
       """Return a version of this JointDistribution object that has parameters fixed to the supplied values"""
       return JointDistribution(self.analyses.values(), pars)

    def biased_sample(self, N, bias=1):
       """Sample from biased versions of all analyses and return them along their with sampling probability. For use in importance sampling.
        
       :param N: Number of samples to draw
       :type N: int
       :param bias: indicates how many 'sigma' of upward bias to apply to the sample 
               generation, computed, in terms of sqrt(variance) of the background.
               Bias only applied to 'signal' parameters, not nuisance parameters.
               NOTE: This doesn't really work properly. Importance sampling is a bit tricky, 
               probably need a smarter way of choosing the 'importance' distribution. (default value=1)
       :type bias: float, optional
       """
       biased_analyses = copy.deepcopy(self.analyses)
       for a in biased_analyses.values():
           a.SR_b = a.SR_b + bias*np.sqrt(a.SR_b)
       biased_joint = JointDistribution(biased_analyses.values(), self.pars, pre_scaled_pars=True)
       samples = biased_joint.sample(N)
       logw = self.log_prob(samples) - biased_joint.log_prob(samples) # log(weight) for each sample
       return samples, logw

    def prepare_pars(self,pars,pre_scaled_pars=False):
        """Prepare default nuisance parameters and return scaled signal and nuisance parameters for each analysis
           (scaled such that MLE's in this parameterisation have
           variance of approx. 1"""
        all_pars = {}
        #print("in prepare_pars; pars:",pars)
        for a in self.analyses.values():
            if a.name not in pars.keys(): raise KeyError("Parameters for analysis {0} not found!".format(a.name))
            #print("  pars[{0}] = {1}".format(a.name,pars[a.name]))
            # Apply scaling if not already done
            # It is an error to scale TensorFlow 'Variable' objects! These
            # should not be used as input unless it is occurring internally in
            # the TensorFlow optimizer. In which case the parameters should already
            # be scaled.
            if pre_scaled_pars:
                p = pars[a.name]
            else:
                try:
                    p_tf = c.convert_to_TF_constants(pars[a.name],ignore_variables=False)
                except TypeError as e:
                    msg = "TensorFlow 'Variable' objects found in the input parameter dictionary for analysis {0}, but parameters are not flagged as 'pre-scaled'! Please do not use this type for input to JointDistribution parameters, as it is reserved for internal use with the TensorFlow optimizer routines, and needs to be controlled to maintain the correct graph relationships between input parameters and the log_prob output of the JointDistribution. Any other list/tuple/array type structure should be used instead."  
                    raise TypeError(msg) from e
                p = a.scale_pars(p_tf)

            # Throw warning about discarded parameters, in case user messed up the input
            missing = []
            for par in pars[a.name].keys():
                if par not in p.keys():
                    missing += [par]
            if len(missing)>0:
                msg = "***WARNING: the following unrecognised parameters were found in the parameter dictionary for analysis {0}: {1}\nThis is permitted, but please make sure it wasn't an accident.".format(a.name, missing)
                print(msg)
            #print("  p: ",p)
            # Add default values for nuisance parameters if not provided
            all_pars[a.name] = a.add_default_nuisance(p)
        #print("   all_pars:", all_pars)
        return all_pars 

    def descale_pars(self,pars):
        """Remove scaling from parameters. Assumes they have all been scaled and require de-scaling."""
        descaled_pars = {}
        for a in self.analyses.values():
          if a.name in pars.keys():
            descaled_pars[a.name] = a.descale_pars(pars[a.name])
        return descaled_pars

    def get_nuis_parameters(self,samples,fixed_pars):
        """Samples vector and signal provided to compute good starting guesses for parameters
           (in scaled parameter space)"""
        pars = {}
        all_fixed_pars = {}
        for a in self.analyses.values():
            p, fp = a.get_nuisance_parameters(self.get_samples_for(a.name,samples),fixed_pars[a.name])                         # Apply scaling to all parameters, so that scan occurs in ~unit scale parameter space
            pars[a.name] = a.scale_pars(p)
            all_fixed_pars[a.name] = a.scale_pars(fp)
        #print("pars:", c.print_with_id(pars,id_only))
        #print("fixed_pars:", c.print_with_id(fixed_pars,id_only))
        return pars, all_fixed_pars

    def get_samples_for(self,name,samples):
        """Extract the samples for a specific analysis from a sample dictionary, and
           remove the analysis name prefix from the keys"""
        d = {key:val for key,val in samples.items() if key.startswith("{0}::".format(name))}
        return c.remove_prefix(name,d)

    def get_all_parameters(self,samples,fixed_pars={}):
        """Samples vector and signal provided to compute good starting guesses for parameters
           (in scaled parameter space)"""
        pars = {}
        all_fixed_pars = {}
        anynan = False
        nanpar = ""
        # TODO: Add error checking for analysis names in fixed_pars dict? But could be useful to allow
        # "extra" analyses to be in there. Perhaps make check optional via a flag (default on)?
        for a in self.analyses.values():
            p, fp = a.get_all_parameters(self.get_samples_for(a.name,samples), fixed_pars.get(a.name,{}))
            # Check the starting guesses are valid
            for pardicts_in in [p,fp]: 
                for par, val in pardicts_in.items():
                    if tf.math.reduce_any(tf.math.is_nan(val)):
                        anynan = True
                        nanpar += "\n    {0}::{1}".format(a.name,par)
            # Apply scaling to all parameters, so that scan occurs in ~unit scale parameter space
            pars[a.name] = a.scale_pars(p)
            all_fixed_pars[a.name] = a.scale_pars(fp)
        if anynan:
            msg = "NaNs detected in parameter starting guesses! The samples used to inform the starting guesses may be invalid (e.g. negative counts for Poisson variables). Parameter starting guess arrays containing NaNs were:{0}".format(nanpar)
            raise ValueError(msg)
        return pars, fixed_pars

    def get_sample_structure(self):
        """Returns a dictionary whose structure is the same as samples from the joint PDF"""
        out = {}
        for a in self.analyses.values():
            out.update(c.add_prefix(a.name,a.get_sample_structure()))
        return out

    def get_parameter_structure(self):
        """Returns three dictionaries whose structure explains how parameters should be supplied
           to this object"""
        free  = {a.name: a.get_free_parameter_structure() for a in self.analyses.values()}
        fixed = {a.name: a.get_fixed_parameter_structure() for a in self.analyses.values()}
        nuis  = {a.name: a.get_nuisance_parameter_structure() for a in self.analyses.values()} 
        return free, fixed, nuis

    def fit_nuisance(self,samples,fixed_pars,log_tag='',verbose=False):
        """Fit nuisance parameters to samples for a fixed signal
           (ignores parameters that were used to construct this object)"""
        fp = c.convert_to_TF_constants(fixed_pars)
        all_nuis_pars, all_fixed_pars = self.get_nuis_parameters(samples,fp)

        # Note, parameters obtained from get_nuis_parameters, and passed to
        # the 'optimize' function, are SCALED. All of them, regardless of whether
        # they actually vary in this instance.
        joint_fitted, q, all_pars, fitted_pars, const_pars = optimize(all_nuis_pars,all_fixed_pars,self.analyses,samples,log_tag=log_tag,verbose=verbose)

        # Make sure to de-scale parameters before returning them to users!
        # Also it is nice to pack up the various parameter splits into a dictionary
        par_dict = {}
        par_dict["all"] = self.descale_pars(all_pars)
        par_dict["fitted"] = self.descale_pars(fitted_pars)
        par_dict["fixed"]  = self.descale_pars(const_pars)
        return q, joint_fitted, par_dict 


    # TODO: Deprecated, but may need something like this again.
    #def fit_nuisance_and_scale(self,signal,samples,log_tag='',verbose=False):
    #    """Fit nuisance parameters plus a signal scaling parameter
    #       (ignores parameters that were used to construct this object)"""
    #    pars = self.get_nuis_parameters(signal,samples)
    #    # Signal scaling parameter. One per sample, and per signal input
    #    Nsamples = list(samples.values())[0].shape[0]
    #    Nsignals = list(list(signal.values())[0].values())[0].shape[0] 
    #    muV = tf.Variable(np.zeros((Nsamples,Nsignals,1)),dtype=c.TFdtype)
    #    # Function to produce signal parameters from mu
    #    def mu_to_sig(pars):
    #        mu = tf.sinh(pars['mu']) # sinh^-1 is kind of like log, but stretches similarly for negative values. Seems to be pretty good for this.
    #        sig_out = {}
    #        for ka,a in signal.items():
    #            sig_out[ka] = {}
    #            for kp,p in a.items():
    #                sig_out[ka][kp] = mu*p
    #        nuis_pars = {k:v for k,v in pars.items() if k is not 'mu'}
    #        out = c.deep_merge(nuis_pars,sig_out) # Return all signal and nuisance parameters, but not mu.
    #        return out 
    #    pars['mu'] = muV # TODO: Not attached to any analysis, but might work anyway  
    #    joint_fitted, q = optimize(pars,None,self.analyses,samples,pre_scaled_pars='nuis',transform=mu_to_sig,log_tag=log_tag,verbose=verbose)
    #    return q, joint_fitted, pars
  
    def fit_all(self,samples,fixed_pars={},log_tag='',verbose=False):
        """Fit all signal and nuisance parameters to samples
           (ignores parameters that were used to construct this object)
           Some special parameters within analyses are also flagged as
           un-fittable, e.g. theory uncertainty parameters. If these
           aren't provided then analyses will use default fixed values,
           but they can be supplied via the "fixed_pars" dict. These
           parameters are always treated as fixed, when it comes to
           starting MLE guesses etc.
        """
        # Make sure the samples are TensorFlow objects of the right type:
        samples = {k: tf.constant(x,dtype="float32") for k,x in samples.items()}
        fp = c.convert_to_TF_constants(fixed_pars)
        all_free_pars, all_fixed_pars = self.get_all_parameters(samples,fp)

        # Note, parameters obtained from get_all_parameters, and passed to
        # the 'optimize' function, are SCALED. All of them, regardless of whether
        # they actually vary in this instance.
        joint_fitted, q, all_pars, fitted_pars, const_pars = optimize(all_free_pars,all_fixed_pars,self.analyses,samples,log_tag=log_tag,verbose=verbose)

        # Make sure to de-scale parameters before returning them to users!
        # Also it is nice to pack up the various parameter splits into a dictionary
        par_dict = {}
        par_dict["all"] = self.descale_pars(all_pars)
        par_dict["fitted"] = self.descale_pars(fitted_pars)
        par_dict["fixed"]  = self.descale_pars(const_pars)
        return q, joint_fitted, par_dict 

    def cat_pars(self,pars):
        """Stack tensorflow parameters in known order"""
        parlist = []
        maxdims = {}
        for ka,a in pars.items():
            for kp,p in a.items():
                parlist += [p]
                i = -1
                for d in p.shape[::-1]:
                    if i not in maxdims.keys() or maxdims[i]<d: maxdims[i] = d
                    i-=1
        maxshape = [None for i in range(len(maxdims))]
        for i,d in maxdims.items():
            maxshape[i] = d

        # Attempt to broadcast all inputs to same shape
        matched_parlist = []
        bcast = tf.broadcast_to(tf.constant(np.ones([1 for d in range(len(maxdims))]),dtype=c.TFdtype),maxshape)
        for p in parlist:
            matched_parlist += [p*bcast]
        return tf.Variable(tf.concat(matched_parlist,axis=-1),name="all_parameters")               

    def uncat_pars(self,catted_pars,pars_template=None):
        """De-stack tensorflow parameters back into separate variables of
           shapes know to each analysis. Assumes stacked_pars are of the
           same structure as pars_template"""
        if pars_template is None: pars_template = self.pars
        pars = {}
        i = 0
        for ka,a in pars_template.items():
            pars[ka] = {}
            for kp,p in a.items():
                N = p.shape[-1]
                pars[ka][kp] = catted_pars[...,i:i+N]
                i+=N
        return pars

    def Hessian(self,pars,samples):
        """Obtain Hessian matrix (and grad) at input parameter point
           Make sure to use de-scaled parameters as input!"""

        # Stack current parameter values to single tensorflow variable for
        # easier matrix manipulation
        catted_pars = self.cat_pars(pars)
        #print("catted_pats:", catted_pars)
        with tf.GradientTape(persistent=True) as tape:
            inpars = self.uncat_pars(catted_pars) # need to unstack for use in each analysis
            joint = JointDistribution(self.analyses.values(),inpars)
            q = -2*joint.log_prob(samples)
            grads = tape.gradient(q, catted_pars)
        #print("grads:", grads)
        hessians = tape.batch_jacobian(grads, catted_pars) # batch_jacobian takes first (the sample) dimensions as independent for much better efficiency
        #print("H:",hessians)
        # Remove the singleton dimensions. We should not be computing Hessians for batches of signal hypotheses. TODO: throw better error if dimension sizes not 1
        grads_out = tf.squeeze(grads,axis=[-2])
        hessians_out = tf.squeeze(hessians,axis=[-2,-4])
        #print("g_out:",grads_out)
        #print("H_out:",hessians_out)
        return hessians_out, grads_out

    def decomposed_parameters(self,pars):
        """Separate input parameters into 'interest' and 'nuisance' lists,
           keeping tracking of their original 'indices' w.r.t. catted format.
           Mainly used for decomposing Hessian matrix."""
        interest_i = {} # indices of parameters in Hessian/stacked pars
        nuisance_i = {}
        interest_p = {} # parameters themselves
        nuisance_p = {}         
        i = 0
        for ka,a in pars.items():
            interest_p[ka] = {}
            nuisance_p[ka] = {}
            interest_i[ka] = {}
            nuisance_i[ka] = {}
            for kp,p in a.items():
                N = p.shape[-1]
                if kp=='theta': #TODO need a more general method for determining which are the nuisance parameters
                    nuisance_p[ka][kp] = p
                    nuisance_i[ka][kp] = (i, N)
                else:
                    interest_p[ka][kp] = p
                    interest_i[ka][kp] = (i, N)
                i+=N
        return interest_i, interest_p, nuisance_i, nuisance_p

    def sub_Hessian(self,H,pari,parj,idim=-1,jdim=-2):
        """Extract sub-Hessian matrix from full Hessian H,
           using dictionary that provides indices for selected
           parameters in H"""
        ilist = []
        for ai in pari.values():
            for i,Ni in ai.values():
                ilist += [ix for ix in range(i,i+Ni)] 
        jlist = []
        for aj in parj.values():
            for j,Nj in aj.values():
                jlist += [jx for jx in range(j,j+Nj)] 
        #print("H.shape:",H.shape)
        #print("ilist:",ilist)
        #print("jlist:",jlist)
        # Use gather to extract row/column slices from Hessian
        subH_i = tf.gather(H,      ilist, axis=idim)
        subH   = tf.gather(subH_i, jlist, axis=jdim)
        return subH

    def sub_grad(self,grad,pari,idim=-1):
        """Extract sub-gradient vector from full grad,
           using dictionary that provides indices for selected
           parameters in grad"""
        ilist = []
        for ai in pari.values():
            for i,Ni in ai.values():
                ilist += [ix for ix in range(i,i+Ni)] 
        sub_grad = tf.gather(grad, ilist, axis=idim)
        return sub_grad

    def decompose_Hessian(self,H,parsi,parsj):
        """Decompose Hessian matrix into
           parameter blocks"""
        Hii = self.sub_Hessian(H,parsi,parsi)
        Hjj = self.sub_Hessian(H,parsj,parsj)
        Hij = self.sub_Hessian(H,parsi,parsj) #Off-diagonal block. Symmetric so we don't need both.
        return Hii, Hjj, Hij

    def quad_loglike_prep(self,samples):
        """Compute second-order Taylor expansion of log-likelihood surface
           around input parameter point(s), and compute quantities needed
           for analytic determination of profile likelihood for fixed signal
           parameters, under this approximation."""
        pars = self.descale_pars(self.pars) # Make sure to use non-scaled parameters to get correct gradients etc.
        #print("Computing Hessian and various matrix operations for all samples...")
        H, g = self.Hessian(pars,samples)
        #print("g:", g) # Should be close to zero if fits worked correctly
        interest_i, interest_p, nuisance_i, nuisance_p = self.decomposed_parameters(pars)
        #print("self.pars:", self.pars)
        #print("descaled_pars:", pars)
        #print("samples:", samples)
        #print("interest_p:", interest_p)
        #print("nuisance_p:", nuisance_p)
        Hii, Hnn, Hin = self.decompose_Hessian(H,interest_i,nuisance_i)
        Hnn_inv = tf.linalg.inv(Hnn)
        gn = self.sub_grad(g,nuisance_i)
        A = tf.linalg.matvec(Hnn_inv,gn)
        B = tf.linalg.matmul(Hnn_inv,Hin) #,transpose_b=True) # TODO: Not sure if transpose needed here. Doesn't seem to make a difference, which seems a little odd.
        #print("...done!")
        return A, B, interest_p, nuisance_p

    def quad_loglike_f(self,samples):
        """Return a function that can be used to compute the profile log-likelihood
           for fixed signal parametes, for many signal hypotheses, using a 
           second-order Taylor expandion of the likelihood surface about a point to
           determine the profiled nuisance parameter values. 
           Should be used after pars are fitted to the global best fit for best
           expansion."""
        A, B, interest_p, nuisance_p = self.quad_loglike_prep(samples)
        f = mm.tools.func_partial(self.neg2loglike_quad,A=A,B=B,interest=interest_p,nuisance=nuisance_p,samples=samples)
        return f

    def neg2loglike_quad(self,signal,A,B,interest,nuisance,samples):
        """Compute -2*loglikelihood using pre-computed Taylor expansion
           parameters (for many samples) for a set of signal hypotheses"""
        # Stack signal parameters into appropriate vector for matrix operations
        parlist = []
        for ka,a in interest.items():
            if ka not in signal.keys():
                raise ValueError("No test signals provided for analysis {0}".format(ka)) 
            for kp in a.keys():
                if kp not in signal[ka].keys():
                    raise ValueError("No test signals provided for parameter {0} in analysis {1}".format(kp,ka))
                parlist += [signal[ka][kp]]
        s = tf.constant(tf.concat(parlist,axis=-1),name="all_signal_parameters")
        s_0 = self.cat_pars(interest) # stacked interest parameter values at expansion point
        theta_0 = self.cat_pars(nuisance) # stacked nuisance parameter values at expansion point
        #print("theta_0.shape:",theta_0.shape)
        #print("A.shape:",A.shape)
        #print("B.shape:",B.shape)
        #print("s.shape:",s.shape)
        #print("s_0.shape:",s_0.shape)
        theta_prof = theta_0 - tf.expand_dims(A,axis=1) - tf.linalg.matvec(tf.expand_dims(B,axis=1),tf.expand_dims(s,axis=0)-s_0)
        #theta_prof = theta_0 - tf.linalg.matvec(tf.expand_dims(B,axis=1),tf.expand_dims(s,axis=0)-s_0) # Ignoring grad term
        # de-stack theta_prof
        theta_prof_dict = self.uncat_pars(theta_prof,pars_template=nuisance)
        #print("theta_prof_dict:", theta_prof_dict)
        #print("signal:", signal)
        # Compute -2*log_prop
        joint = JointDistribution(self.analyses.values(),c.deep_merge(signal,theta_prof_dict))
        q = -2*joint.log_prob(samples)
        return q
