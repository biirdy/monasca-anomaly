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

class RDEAnomalyProcessor(AnomalyProcessor):

	def __init__(self):
		AnomalyProcessor.__init__(self, cfg.CONF.rde.kafka_group)
		rde_config = cfg.CONF.rde

		# hostname -> anom vlaues 
		self._anom_values = {}

		#params - TODO; load in from config file
		self.anom_threshold 	= rde_config.anom_threshold 
		self.fault_threshold 	= rde_config.fault_threshold
		self.normal_threshold 	= rde_config.normal_threshold

		#metric to aggregate - should put these in config and load in
		self.metrics = ['cpu.user_perc', 'cpu.system_perc']

		# hostname -> sample
		self._sample_buffer = {}

	def _send_predictions(self, metric_id, metric_envelope):
		# get host name
		metric 		= metric_envelope['metric']
		name 		= metric['name']
		dimensions 	= metric['dimensions']
		vlaue 		= metric['value']
		hostname	= dimensions['hostname']

		# fill buffer
		self._sample_buffer[hostname][name] = value;

		print("Received " + name + " for host " + hostname)

		# if buffer is full process sample
		if len(self._sample_buffer[hostname]) == len(self.metrics):

			print("Sample buffer full for " + hostname)

			sample = []

			# loop through to create sample list so features are always in the same order 
			# rather than using .values()
			for x in self.metrics:
				sample.append(self._sample_buffer[hostname][x])

			#send to anomaly detection
			anom_values = rde(sample, hostname)

			#publish results - should remove dimensions to avoid confusion
			metric['name'] = hostname + '.rde_multi.' + ".".join(self.metrics) +  '.anomaly_score'
	       	metric['value'] = anom_values['status']
	       	str_value = simplejson.dumps(metric_envelope)
	        self._producer.send_messages(self._topic, str_value)

	        # clear sample buffer
	        for x in self.metrics:
	        	del self._sample_buffer[hostname][x]

	def rde(sample, hostname):

		#first iteration of hostname - init anomaly values
		if hostname not in self._anom_values:
			self._anom_values[hostname] = {
				'mean': value, 
				'density': 1.0,
				'mean_density': 1.0, 
				'scalar': numpy.linalg.norm(numpy.array(value))**2,
				'k': 2,
				'ks': 2,
				'status': 0,
				'normal_flag': 0,
				'fault_flag': 0
			}
			anom_values = self._anom_values[hostname]
		#original RDE
		else:
			#bring local anomaly values
			anom_values = self._anom_values[hostname]	
				
			anom_values['mean'] 		= (((anom_values['k']-1)/anom_values['k'])*anom_values['mean'])+((1/anom_values['k'])*value)	 
			anom_values['scalar'] 		= (((anom_values['k']-1)/anom_values['k'])*anom_values['scalar'])+((1/anom_values['k'])*(numpy.linalg.norm(numpy.array(value))**2))
			anom_values['p_density']	= anom_values['density']
			anom_values['density']		= 1/(1 + ((numpy.linalg.norm(numpy.array(value - anom_values['mean'])))**2) + anom_values['scalar'] - (numpy.linalg.norm(numpy.array(anom_values['mean']))**2))
			diff						= abs(anom_values['density'] - anom_values['p_density'])
			anom_values['mean_density']	= ((((anom_values['ks']-1)/anom_values['ks'])*anom_values['mean_density'])+((1/anom_values['ks'])*anom_values['density']))*(1 - diff)+(anom_values['density'] * diff)
	
			#anomaly detection
			if anom_values['status'] == 0:
				if anom_values['density'] < anom_values['mean_density'] * self.anom_threshold:
					anom_values['fault_flag'] += 1
					if anom_values['fault_flag'] >= self.fault_threshold:
						anom_values['status'] 		= 1
						anom_values['ks'] 		= 0
						anom_values['fault_flag'] 	= 0
			else:
				if anom_values['density'] >= anom_values['mean_density']:
					anom_values['normal_flag'] += 1
					if anom_values['normal_flag'] >= self.normal_threshold:
						anom_values['status'] 		= 0
						anom_values['ks'] 		= 0
						anom_values['normal_flag']	= 0

			anom_values['ks'] += 1
			anom_values['k'] += 1

		return anom_values

	        
