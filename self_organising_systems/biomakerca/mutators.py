"""Mutators of agent programs for in-environment learning.

These mutators are intended to be used inside an environment to allow for 
autonomous reproduction with variation of agents.

Nothing stops these mutators to be used in an outer loop, for instance in 
picbreeder-like experiments.

Copyright 2023 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
from abc import ABC, abstractmethod
import math

import jax.numpy as jp
import jax.random as jr
from self_organising_systems.biomakerca.utils import stringify_class

class Mutator(ABC):
  """Interface of all asexual mutators.

  The abstract methods need to be implemented in order to allow for 
  in-environment mutations, through the method step_maker.step_env.
  """

  @abstractmethod
  def initialize(self, key, p):
    """Initialize mutation parameters.

    p must be one-dimensional.
    Return a concatenation of p and the mutation parameters.
    Advice: it is better if related params are contiguous to one another. This
    is because SexualMutators likely use crossing over as a mechanism.
    """
    pass

  @abstractmethod
  def split_params(self, p):
    """Given all parameters, separate model params and the mutation params.
    
    p must be one-dimensional.
    """
    pass

  @abstractmethod
  def mutate(self, key, p):
    """Mutate params.
    
    The input must be all params, as generated by 'initialize'.
    This method can mutate mutation params too.
    p must be one-dimensional.
    """
    pass

  def __str__(self):
    return stringify_class(self)


class BasicMutator(Mutator):
  """Simple mutator that varies parameters with a constant standard deviation.
  
  Attributes:
    sd: standard deviation of the mutation for each parameter.
    change_perc: the avg percentage of parameters that are changed at each 
      mutate call.
  """

  def __init__(self, sd, change_perc):
    self.sd = sd
    self.change_perc = change_perc

  def initialize(self, key, p):
    # not batched, necessarily
    return p

  def split_params(self, p):
    # not batched.
    return p, jp.empty(0)

  def mutate(self, key, p):
    # not batched.

    # usually, you would have to split params here first. But here there are no
    # mutation params.

    k1, k2 = jr.split(key)
    dp = jr.normal(k1, p.shape) * self.sd * (
        jr.uniform(k2, p.shape) < self.change_perc).astype(jp.float32)
    return p + dp


class RandomlyAdaptiveMutator(Mutator):
  """Mutator that also randomly mutates the standard deviation of each parameter.
  
  Each parameter has a respective standard deviation sd. This sd is used to 
  sample the offspring's new parameter from a gaussian.
  Each sd is also randomly modified by sampling a gaussian with a fixed 
  meta_sd_perc. meta_sd_perc is a percentage of the current value of sd.
  
  Attributes:
    init_sd: the initial value of sd for all parameters.
    change_perc: the avg percentage of parameters that are changed at each 
      mutate call. If a parameter is not mutated, neither is its sd. change_perc
      can be set to None to mean that all parameters are mutated at every step.
    meta_sd_perc: each sd is mutated by sampling a gaussian centered in sd and
      with a meta_sd of: (meta_sd_perc*sd).
    min_sd: minimum value of sd for any param.
    max_sd: maximum value of sd for any param.
  """

  def __init__(self, init_sd, change_perc: float | None, meta_sd_perc=0.1,
               min_sd=1e-5, max_sd=1e1):
    self.init_sd = init_sd
    self.change_perc = change_perc
    self.meta_sd_perc = meta_sd_perc
    self.min_sd = min_sd
    self.max_sd = max_sd

  def join_params(self, p, sd):
    return jp.ravel(jp.column_stack((p, sd)))

  def initialize(self, key, p):
    # Does not work if batched. (use vmap)
    sd = jp.full_like(p, self.init_sd)
    return self.join_params(p, sd)

  def split_params(self, p):
    # works also if batched.
    p_e, sd_e = jp.split(
        p.reshape(p.shape[:-1]+ (p.shape[-1]//2, 2)), 2, axis=-1)
    return p_e[..., 0], sd_e[..., 0]

  def mutate(self, key, p):
    # not batched.

    mu, sd = self.split_params(p)

    ku, key = jr.split(key)
    dmu = jr.truncated_normal(ku, -3, 3, sd.shape) * sd

    # mutate sd now.
    ku, key = jr.split(key)
    dsd = jr.truncated_normal(ku, -3, 3, sd.shape) * sd * self.meta_sd_perc

    if self.change_perc is not None:
      ku, key = jr.split(key)
      mask = (jr.uniform(ku, sd.shape) < self.change_perc).astype(jp.float32)
      dmu *= mask
      dsd *= mask

    new_mu = mu + dmu
    new_sd = (sd + dsd).clip(self.min_sd, self.max_sd)

    return  self.join_params(new_mu, new_sd)

### Sexual mutators


class SexualMutator(ABC):
  """Interface of all sexual mutators.

  The abstract methods need to be implemented in order to allow for 
  in-environment sexual mutations, through the method step_maker.step_env.

  For now, a SexualMutator does not accept variable parameters.
  This is because it uses a Mutator for extra variation.
  Ideally, the Mutator used is the same of asexual reproduction, but you can
  put custom ones here as well.
  """

  def __init__(self, mutator: Mutator):
    """Set the mutator for the general variation of parameters.
    """
    self.mutator = mutator

  @abstractmethod
  def mutate(self, key, p1, p2):
    """Mutate params.

    The inputs p1 and p2 must be all params, as generated by 'initialize'.
    This method can mutate mutation params too.
    p must be one-dimensional.
    """
    pass

  def __str__(self):
    return stringify_class(self)


def valuenoise1d(key, f, n, interpolation="linear"):
  """Create continous noise, useful for make a XOver mask."""
  ku, key = jr.split(key)
  grads = jr.uniform(ku, (f,), minval=-1, maxval=1)
  n_repeats = int(math.ceil(n/(f-1)))
  prev = jp.repeat(grads[:-1], n_repeats, axis=0)[:n]
  next = jp.repeat(grads[1:], n_repeats, axis=0)[:n]
  # fraction of where you are
  t = jp.tile(jp.linspace(0, 1, n_repeats+1)[:-1], f-1)[:n]

  # linear interpolation because we don't need anything more complex.
  # if you want it cubic, do this:
  if interpolation == "cubic":
    t = t*t*t*(t*(t*6 - 15) + 10)
  return prev + t * (next - prev)


class CrossOverSexualMutator(SexualMutator):
  """Sexual mutator that performs a classic Crossing over recombination mutation.

  This mutator also performs an asexual mutation through the mutator input.
  """

  def __init__(self, mutator: Mutator, n_frequencies=16):
    super().__init__(mutator)
    self.n_frequencies = n_frequencies

  def mutate(self, key, p1, p2):
    """Mutate params.

    The inputs p1 and p2 must be all params, as generated by 'initialize'.
    This method can mutate mutation params too.
    p must be one-dimensional.
    """
    ku, key = jr.split(key)
    xo_mask = valuenoise1d(ku, self.n_frequencies, p1.shape[0], "linear") > 0

    new_p = xo_mask * p1 + (1. - xo_mask) * p2

    ku, key = jr.split(key)
    return self.mutator.mutate(ku, new_p)

