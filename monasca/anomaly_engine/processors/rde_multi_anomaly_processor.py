from __future__ import division

import sys
import numpy
import math
import simplejson
import random

from oslo.config import cfg
from monasca.openstack.common import log
from anomaly_processor import AnomalyProcessor

LOG = log.getLogger(__name__)

class RDEMultiAnomalyProcessor(AnomalyProcessor):

	def __init__(self):
		AnomalyProcessor.__init__(self, cfg.CONF.rde_multi.kafka_group)
		rde_config = cfg.CONF.rde
		rde_multi_config = cfg.CONF.rde_multi

		# hostname -> anom vlaues 
		self._anom_values = {}

		#params - TODO; load in from config file
		self.anom_threshold 	= rde_config.anom_threshold 
		self.fault_threshold 	= rde_config.fault_threshold
		self.normal_threshold 	= rde_config.normal_threshold

		# what dimension to match the samples on - LIST
		self.dimension_match = rde_multi_config.dimension_match

		# what to name the sample when publishing back - log file!
		self.sample_name = rde_multi_config.sample_name

		#metric to aggregate - should put these in config and load in
		self.metrics = rde_multi_config.sample_metrics

		# hostname -> sample
		self._sample_buffer = {}

	def _send_predictions(self, metric_id, metric_envelope):
		# get host name
		metric 		= metric_envelope['metric']
		name 		= metric['name']
		dimensions 	= metric['dimensions']
		value 		= metric['value']
		
		# string containing the dimensions to metrics by
		# e.g "compute1.eth0" or "compute2./home/mon-agent/commsmain-2.pcap"
		match_str 	= '.'.join([dimensions[x] for x in self.dimension_match])	# should check dimensions actually exists

		# fill buffer
		if match_str not in self._sample_buffer:
			self._sample_buffer[match_str] = {}
		
		self._sample_buffer[match_str][name] = value;

		print("Received " + name + " for " + self.dimension_match + " "  + match_str)

		# if buffer is full process sample
		if len(self._sample_buffer[match_str]) == len(self.metrics):

			print("Sample buffer full for " + match_str)

			#create sample
			sample = [self._sample_buffer[match_str][x] for x in self.metrics]

			#send to anomaly detection
			anom_values = self.rde(sample, match_str)

			#remove all dimensions except the ones were matching on
			match = {x: dimensions[x] for x in self.dimension_match}
			metric['dimensions'].clear()
			metric['dimensions'] = match 

			#publish results	
			# anomaly score
			metric['name'] = self.sample_name + '.rde.anomaly_score'
			metric['value'] = anom_values['status']
			str_value = simplejson.dumps(metric_envelope)
			self._producer.send_messages(self._topic, str_value)

			# density
			metric['name'] = self.sample_name + '.rde.density'
			metric['value'] = anom_values['density']
			str_value = simplejson.dumps(metric_envelope)
			self._producer.send_messages(self._topic, str_value)			

			# mean density
			metric['name'] = self.sample_name + '.rde.mean_density'
			metric['value'] = anom_values['mean_density']
			str_value = simplejson.dumps(metric_envelope)
			self._producer.send_messages(self._topic, str_value)

			# clear sample buffer
			for x in self.metrics:
				del self._sample_buffer[match_str][x]

	def rde(self, sample, match_str):

		#first iteration of match_str - init anomaly values
		if match_str not in self._anom_values:
			self._anom_values[match_str] = {
				'mean': sample, 
				'density': 1.0,
				'mean_density': 1.0, 
				'scalar': numpy.linalg.norm(numpy.array(sample))**2,
				'k': 2,
				'ks': 2,
				'status': 0,
				'normal_flag': 0,
				'fault_flag': 0
			}
			anom_values = self._anom_values[match_str]

		#original RDE
		else:
			#bring local anomaly values
			anom_values = self._anom_values[match_str]	
				
			anom_values['mean'] 		= [(((anom_values['k']-1)/anom_values['k'])*anom_values['mean'][idx])+((1/anom_values['k'])*x) for idx,x in enumerate(sample)]	 
			anom_values['scalar'] 		= (((anom_values['k']-1)/anom_values['k'])*anom_values['scalar'])+((1/anom_values['k'])*(numpy.linalg.norm(numpy.array(sample))**2))
			anom_values['p_density']	= anom_values['density']
			anom_values['density']		= 1/(1 + ((numpy.linalg.norm(numpy.array([x - y for x,y in zip(sample, anom_values['mean'])])))**2) + anom_values['scalar'] - (numpy.linalg.norm(numpy.array(anom_values['mean']))**2))
			diff						= abs(anom_values['density'] - anom_values['p_density'])
			anom_values['mean_density']	= ((((anom_values['ks']-1)/anom_values['ks'])*anom_values['mean_density'])+((1/anom_values['ks'])*anom_values['density']))*(1 - diff)+(anom_values['density'] * diff)
	
			#anomaly detection
			if anom_values['status'] == 0:
				if anom_values['density'] < anom_values['mean_density'] * self.anom_threshold:
					anom_values['fault_flag'] += 1
					if anom_values['fault_flag'] >= self.fault_threshold:
						anom_values['status'] 		= 1
						anom_values['ks'] 			= 0
						anom_values['fault_flag'] 	= 0
			else:
				if anom_values['density'] >= anom_values['mean_density']:
					anom_values['normal_flag'] += 1
					if anom_values['normal_flag'] >= self.normal_threshold:
						anom_values['status'] 		= 0
						anom_values['ks'] 			= 0
						anom_values['normal_flag']	= 0

			anom_values['ks'] += 1
			anom_values['k'] += 1

		print("------------------------------------")
		print(str(anom_values))

		return anom_values

	        
