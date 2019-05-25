import numpy as np
import time as _timer_
from utility_funcs import *
import string, random, os
from scipy import optimize
from scipy.special import erf


class HeatSolver:
	'''
	Solves two-phase thermal diffusivity problem with a temperature-dependent thermal conductivity of ice in
	two-dimensions. Sources and sinks include latent heat of fusion and tidal heating
	Options:
		tidalheat -- binary; turns on/off viscosity-dependent tidal heating from Mitri & Showman (2005), default = 0
		Ttol -- convergence tolerance for temperature, default = 0.1 K
		phitol -- convergence tolerance for liquid fraction, default = 0.01
		latentheat -- 1 : use Huber et al. (2008) enthalpy method
					  2 : use method to modify heat capcity, e.g. Michaut and Manga, 2014 or Hesse et al., 2018
					    => must choose a solidus and liquidus temperature if using
		freezestop -- binary; stop when sill is frozen, default = 0
	Usage:
		Assuming, model = IceSystem(...)
		- Turn on tidal heating component
			model.tidalheat = True

		- Change tolerances
			model.Ttol = 0.001
			model.phitol = 0.0001
			model.Stol = 0.0001
	'''
	# off and on options
	tidalheat = 0  # turns off or on tidalheating component
	Ttol = 0.1  # temperature tolerance
	phitol = 0.01  # liquid fraction tolerance
	Stol = 1  # salinity tolerance
	latentheat = 1  # choose enthalpy method to use
	freezestop = 0  # stop simulation upon total solidification of sill

	class outputs:
		def choose(self, all=False, T=False, phi=False, k=False, S=False, Q=False, h=False, r=False,
		           freeze_fronts=False, percent_frozen=False, iterations=False, output_frequency=1000, output_list=[]):
			'''
			Choose which outputs to track with time. Each variable is updated at the chosen output frequency and is
			returned in the dictionary object outputs.transient_results.
			Parameters:
				output_frequency : integer
					the frequency to report a transient result. Default is every 1000 time steps
				list : list
					list of strings for the outputs below
				all : binary
					turns on all outputs listed below
				T, phi, k, S, Q : binary
					tracks and returns a list of temperature, liquid fraction, volume averaged thermal conductivity,
					salinity, and source/sink grids, respectively
				h : binary
					tracks the height of the liquid chamber over time into a 1d list
				Ra : binary
					tracks the rayleigh number across the remaining liquid into a 1d list
				freeze_fronts : binary
					tracks the propagating freeze front at the top and bottom of the sill into a 1d list
				percent_frozen : binary
					tracks and returns a 1d list of the percent of the original sill that is now ice
				iterations : binary
					tracks and returns a 1d list of 'iter_k' values from the enthalpy method over time
			'''
			to_output = {'T': T, 'phi': phi, 'k': k, 'S': S, 'Q': Q, 'h': h, 'freeze fronts': freeze_fronts, 'r': r,
			             'percent frozen': percent_frozen, 'iterations': iterations}
			if all: to_output = {key: True for key, value in to_output.items()}
			if len(output_list) != 0:
				for item in output_list: to_output[item] = True

			self.outputs.output_frequency = output_frequency
			self.outputs.transient_results = {'time': []}
			self.outputs.tmp_data_directory = './tmp/'
			self.outputs.tmp_data_file_name = 'tmp_data_runID' + ''.join(random.choice(string.digits) for _ in range(4))
			for key in to_output:
				if to_output[key] is True:
					self.outputs.transient_results[key] = []
			self.outputs.outputs = self.outputs.transient_results.copy()

		def calculate_outputs(self, n):
			'''
			--- THIS COULD PROBABLY BE WRITTEN MORE PYTHONIC
			Calculates the output and appends it to the list for chosen outputs
			Parameters:
				n : integer
					nth time step during simulation
			Returns:
				ans : dictionary object
					dictionary object with chosen outputs as 1d lists
			'''
			ans = {}
			for key in self.outputs.outputs:
				if key == 'time':
					ans[key] = self.model_time
				if key == 'percent frozen':
					ans[key] = 1 - (self.phi[self.geom].sum()) / len(self.geom[1])
				if key == 'r':
					tmp = np.where(self.phi > 0)
					ans[key] = self.dx * max(tmp[1])
					del tmp
				if key == 'h':
					tmp = np.where(self.phi > 0)
					ans[key] = (max(tmp[0]) - min(tmp[0])) * self.dz
					del tmp
				if key == 'freeze fronts':
					tmp = np.where(self.phi > 0)
					ans[key] = np.array([min(tmp[0]), max(tmp[0])]) * self.dz
					del tmp
				if key == 'iterations':
					ans[key] = self.num_iter
				if key == 'T':
					ans[key] = self.T.copy()
				if key == 'S':
					ans[key] = self.S.copy()
				if key == 'phi':
					ans[key] = self.phi.copy()
				if key == 'k':
					ans[key] = self.k.copy()
				if key == 'Q':
					ans[key] = self.Q.copy()
			return ans

		def get_results(self, n):
			if n % self.outputs.output_frequency == 0:
				get = self.outputs.calculate_outputs(self, n)
				save_data(get, self.outputs.tmp_data_file_name + '_n={}'.format(n), self.outputs.tmp_data_directory)

		def get_all_data(self):
			cwd = os.getcwd()
			os.chdir(self.outputs.tmp_data_directory)
			data_list = nat_sort([data for data in os.listdir() if data.endswith('.pkl') and \
			                      self.outputs.tmp_data_file_name in data])
			ans = self.outputs.transient_results.copy()
			for file in data_list:
				tmp_dict = load_data(file)
				for key in self.outputs.outputs:
					ans[key].append(tmp_dict[key])
				del tmp_dict
				os.remove(file)

			for key in self.outputs.outputs:
				ans[key] = np.asarray(ans[key])

			os.chdir(cwd)
			return ans

	def set_boundaryconditions(self, top=True, bottom=True, sides=True):
		'''
			Set boundary conditions for heat solver
			top : top boundary conditions
				default: Dirichlet, Ttop = Tsurf chosen earlier
				'Flux': surface loses heat to a "ghost cell" of ice equal to Tsurf
			bottom: bottom boundary condition
				default: Dirichlet, Tbottom = Tbot chosen earlier
			sides: left and right boundary conditions, forced symmetric
				default: Dirichlet, Tleft = Tright =  Tedge (see init_T)
					* NOTE: must set up domain such that anomaly is far enough away to not interact with the
					edges of domain
				'Reflect' : a 'no flux' boundary condition
		'''
		self.topBC = top
		self.botBC = bottom
		self.sidesBC = sides

	def update_salinity(self, phi_last):
		if self.issalt:
			new_ice = np.where((phi_last > 0) & (self.phi == 0))
			water = np.where(self.phi >= self.rejection_cutoff)
			vol = np.shape(water)[1]
			rejected_salt = 0
			self.removed_salt.append(0)
			if len(new_ice[0]) > 0 and vol != 0:
				for i in range(len(new_ice[0])):
					# save starting salinity in cell
					S_old = self.S[new_ice[0][i], new_ice[1][i]]
					# calculate thermal gradients across each cell
					dTx = abs(self.T[new_ice[0][i], new_ice[1][i] - 1] - self.T[new_ice[0][i], new_ice[1][i] + 1]) / (
							2 * self.dx)
					dTz = (self.T[new_ice[0][i] - 1, new_ice[1][i]] - self.T[new_ice[0][i] + 1, new_ice[1][i]]) / (
							2 * self.dz)
					# brine drainage parameterization:
					#  bottom of sill -> no gravity-drainage, salt stays
					if dTz > 0:
						self.S[new_ice[0][i], new_ice[1][i]] = S_old

					#  top of sill -> brine drains and rejects salt
					elif dTz < 0:
						# dT = np.hypot(dTx, dTz)  # gradient across the diagonal of the cell
						# dT = max(abs(dTx), abs(dTz))  # maximum value
						dT = (abs(dTx) + abs(dTz)) / 2  # average over both
						self.S[new_ice[0][i], new_ice[1][i]] = self.entrain_salt(dT, S_old)
						rejected_salt += S_old - self.S[new_ice[0][i], new_ice[1][i]]

				# assume the salt is well mixed into remaining liquid solution in time step dt
				if vol != 0: self.S[water] = self.S[water] + rejected_salt / vol
				# remove salt from system if liquid is above the saturation point
				self.removed_salt[-1] += (self.S[self.S >= self.saturation_point] - self.saturation_point).sum()
				# ensure liquid hits only the saturation point
				self.S[self.S > self.saturation_point] = self.saturation_point

			# check mass conservation
			total_S_new = self.S.sum() + np.asarray(self.removed_salt).sum()
			if abs(total_S_new - self.total_salt[0]) <= self.Stol:
				self.total_salt.append(total_S_new)
			else:
				self.total_salt.append(total_S_new)
				raise Exception('Mass not being conserved')

			if (self.S[water] >= self.saturation_point).all() and water[0].sum() > 0:
				return 1
			else:
				return 0

	def update_liquid_fraction(self, phi_last):
		if self.issalt == True:
			self.Tm = self.Tm_func(self.S)
		# calculate new enthalpy of solid ice
		Hs = self.cp_i * self.Tm
		H = self.cp_i * self.T + self.constants.Lf * phi_last
		# update liquid fraction
		self.phi[H >= Hs] = (H[H >= Hs] - Hs[H >= Hs]) / self.constants.Lf
		self.phi[H <= Hs + self.constants.Lf] = (H[H <= Hs + self.constants.Lf] - Hs[
			H <= Hs + self.constants.Lf]) / self.constants.Lf
		# all ice
		self.phi[H < Hs] = 0.
		# all water
		self.phi[H > Hs + self.constants.Lf] = 1

	def update_volume_averages(self):
		if self.kT == True:
			self.k = (1 - self.phi) * (self.constants.ac / self.T) + self.phi * self.constants.kw
		else:
			self.k = (1 - self.phi) * self.constants.ki + self.phi * self.constants.kw

		if self.cpT is True:
			self.cp_i = 185. + 2 * 7.037 * self.T
		else:
			self.cp_i = self.constants.cp_i

		if self.issalt:
			self.rhoc = (1 - self.phi) * (self.constants.rho_i + self.Ci_rho * self.S) * self.cp_i + self.phi * (
					self.constants.rho_w + self.C_rho * self.S) * self.constants.cp_w
		elif not self.issalt:
			self.rhoc = (
						            1 - self.phi) * self.constants.rho_i * self.cp_i + self.phi * self.constants.rho_w * self.constants.cp_w

	def update_sources_sinks(self, phi_last, T_last):
		self.latent_heat = self.constants.rho_i * self.constants.Lf * \
		                   (self.phi[1:-1, 1:-1] - phi_last[1:-1, 1:-1]) / self.dt

		self.tidal_heat = 0
		if self.tidalheat == True:
			# ICE effective viscosity follows an Arrenhius law
			#   viscosity = reference viscosity * exp[C/Tm * (Tm/T - 1)]
			# if cell is water, just use reference viscosity for pure ice at 0 K
			self.visc = (1 - self.phi[1:-1, 1:-1]) * self.constants.visc0i \
			            * np.exp(self.constants.Qs * (self.Tm[1:-1, 1:-1] / T_last[1:-1, 1:-1] - 1) / \
			                     (self.constants.Rg * self.Tm[1:-1, 1:-1])) \
			            + self.phi[1:-1, 1:-1] * self.constants.visc0w
			self.tidal_heat = (self.constants.eps0 ** 2 * self.constants.omega ** 2 * self.visc) / (
					2 + 2 * self.constants.omega ** 2 * self.visc ** 2 / (self.constants.G ** 2))

		self.Q = self.tidal_heat - self.latent_heat

	def apply_boundary_conditions(self, T):
		# apply chosen boundary conditions at bottom of domain
		if self.botBC == True:
			self.T[-1, 1:-1] = self.Tbot

		# apply chosen boundary conditions at top of domain
		if self.topBC == True:
			self.T[0, 1:-1] = self.Tsurf

		elif self.topBC == 'Flux':
			T_top_out = self.Tsurf * (self.Tbot / self.Tsurf) ** (-self.dz / self.Lz)
			if self.cpT is True:
				Cbc = self.rhoc[0, 1:-1] / (self.constants.rho_i * (185. + 2 * 7.037 * T_top_out))
			else:
				Cbc = 1
			c = self.dt / (2 * self.rhoc[0, 1:-1])
			Ttopx = c / self.dx ** 2 * ((self.k[0, 1:-1] + self.k[0, 2:]) * (self.T[0, 2:] - self.T[0, 1:-1]) \
			                            - (self.k[0, 1:-1] + self.k[0, :-2]) * (self.T[0, 1:-1] - self.T[0, :-2]))
			Ttopz = c / self.dz ** 2 * ((self.k[0, 1:-1] + self.k[1, 1:-1]) * (self.T[1, 1:-1] - self.T[0, 1:-1]) \
			                            - (self.k[0, 1:-1] + Cbc * self.constants.ac / T_top_out) * (
					                            self.T[0, 1:-1] - T_top_out))
			self.T[0, 1:-1] = self.T[0, 1:-1] + Ttopx + Ttopz + self.Q[0, :] * 2 * c

		# apply chosen boundary conditions at sides of domain
		if self.sidesBC == True:
			self.T[:, 0] = self.Tedge.copy()
			self.T[:, self.nx - 1] = self.Tedge.copy()

		elif self.sidesBC == 'Reflect':
			self.T[:, 0] = self.T[:, 1].copy()
			self.T[:, -1] = self.T[:, -2].copy()

	def print_all_options(self, nt):
		def stringIO(bin):
			if bin:
				return 'on'
			else:
				return 'off'

		def stringBC(BC):
			if isinstance(BC, str):
				return BC
			elif BC:
				return 'Dirichlet'

		print('Starting simulation with\n-------------------------')
		print('\t total model time:  {}s, {}yr'.format(nt * self.dt, (nt * self.dt) / self.constants.styr))
		print('\t surface temperature: {} K'.format(self.Tsurf))
		print('\t bottom temperature:  {} K'.format(self.Tbot))
		print('\t boundary conditions:')
		print('\t    top:     {}'.format(stringBC(self.topBC)))
		print('\t    bottom:  {}'.format(stringBC(self.botBC)))
		print('\t    sides:   {}'.format(stringBC(self.sidesBC)))
		print('\t sources/sinks:')
		print('\t    tidal heating:  {}'.format(stringIO(self.tidalheat)))
		print('\t    latent heat:    {}'.format(stringIO(self.latentheat)))
		print('\t tolerances:')
		print('\t    temperature:     {}'.format(self.Ttol))
		print('\t    liquid fraction: {}'.format(self.phitol))
		if self.issalt:
			print('\t    salinity:        {}'.format(self.Stol))
		print('\t thermal properties:')
		print('\t    ki(T):    {}'.format(stringIO(self.kT)))
		print('\t    ci(T):    {}'.format(stringIO(self.cpT)))
		print('\t intrusion/salt:')
		try:
			self.geom
			print(f'\t    radius:    {self.R_int}m')
			print(f'\t    thickness: {self.thickness}m')
			print(f'\t    depth:     {self.depth}m')
		except:
			pass
		print('\t    salinity: {}'.format(stringIO(self.issalt)))
		if self.issalt:
			print(f'\t       composition:    {self.composition}')
			print(f'\t       concentration:  {self.concentration}ppt')
		print('\t other:')
		print(f'\t     stop on freeze: {stringIO(self.freezestop)}')
		print('-------------------------')
		try:
			print('Requested outputs: {}'.format(list(self.outputs.transient_results.keys())))
		except AttributeError:
			print('no outputs requested')

	def solve_heat(self, nt, dt, print_opts=True, n0=0):
		self.dt = dt
		self.model_time = dt
		start_time = _timer_.clock()
		if print_opts: self.print_all_options(nt)

		for n in range(n0, n0 + nt):
			TErr, phiErr = np.inf, np.inf
			iter_k = 0
			while (TErr > self.Ttol) and (phiErr > self.phitol):
				T_last, phi_last = self.T.copy(), self.phi.copy()

				# constant in front of x-terms
				Cx = self.dt / (2 * self.rhoc[1:-1, 1:-1] * self.dx ** 2)
				# constant in front of z-terms
				Cz = self.dt / (2 * self.rhoc[1:-1, 1:-1] * self.dz ** 2)
				# temperature terms in z direction
				Tz = Cz * ((self.k[1:-1, 1:-1] + self.k[2:, 1:-1]) * (T_last[2:, 1:-1] - T_last[1:-1, 1:-1]) \
				           - (self.k[1:-1, 1:-1] + self.k[:-2, 1:-1]) * (T_last[1:-1, 1:-1] - T_last[:-2, 1:-1]))
				# temperature terms in x direction
				Tx = Cx * ((self.k[1:-1, 1:-1] + self.k[1:-1, 2:]) * (T_last[1:-1, 2:] - T_last[1:-1, 1:-1]) \
				           - (self.k[1:-1, 1:-1] + self.k[1:-1, :-2]) * (T_last[1:-1, 1:-1] - T_last[1:-1, :-2]))

				self.update_liquid_fraction(phi_last=phi_last)
				if self.issalt: self.saturated = self.update_salinity(phi_last=phi_last)
				self.update_volume_averages()
				self.update_sources_sinks(phi_last=phi_last, T_last=T_last)

				self.T[1:-1, 1:-1] = T_last[1:-1, 1:-1] + Tx + Tz + self.Q * self.dt / self.rhoc[1:-1, 1:-1]
				self.apply_boundary_conditions(T=T_last)

				TErr = (abs(self.T[1:-1, 1:-1] - T_last[1:-1, 1:-1])).max()
				phiErr = (abs(self.phi[1:-1, 1:-1] - phi_last[1:-1, 1:-1])).max()

				iter_k += 1
				# kill statement when parameters won't allow solution to converge
				if iter_k > 1000:
					raise Exception('solution not converging')

			# outputs here
			self.num_iter = iter_k
			self.model_time = n * self.dt

			try:
				self.outputs.get_results(self, n=n)
				save_data(self, 'model_runID' + self.outputs.tmp_data_file_name.split('runID')[1] + '.pkl',
				          self.outputs.tmp_data_directory, final=0)
			except AttributeError:
				pass

			if self.freezestop:
				if (len(self.phi[self.phi > 0]) == 0):  # or (self.issalt and self.saturated):
					print('sill frozen at {0:0.04f}s'.format(self.model_time))
					self.run_time = _timer_.clock() - start_time
					return self.model_time

			del T_last, phi_last, Cx, Cz, Tx, Tz, iter_k, TErr, phiErr
		self.run_time = _timer_.clock() - start_time

	class stefan:
		'''
		Solutions to analytical two-phase heat diffusion problem for comparison
		'''

		def solution(self, t, T1, T0):
			if T1 > T0:  # melting regime
				kappa = self.constants.kw / (self.constants.cp_w * self.constants.rho_w)
				Stf = self.constants.cp_w * (T1 - T0) / self.constants.Lf
			elif T1 < T0:  # freezing regime
				T1, T0 = T0, T1
				kappa = self.constants.ki / (self.constants.cp_i * self.constants.rho_i)
				Stf = self.constants.cp_i * (T1 - T0) / self.constants.Lf
			lam = optimize.root(lambda x: x * np.exp(x ** 2) * erf(x) - Stf / np.sqrt(np.pi), 1)['x'][0]

			self.stefan.zm = 2 * lam * np.sqrt(kappa * t)
			self.stefan.zm_func = lambda time: 2 * lam * np.sqrt(kappa * time)
			self.stefan.zm_const = 2 * lam * np.sqrt(kappa)
			# self.stefan_time_frozen = (self.thickness / (2 * lam)) ** 2 / kappa
			self.stefan.z = np.linspace(0, self.stefan.zm)
			self.stefan.T = T1 - (T1 - T0) * erf(self.stefan.z / (2 * np.sqrt(kappa * t))) / erf(lam)

		def compare(self, dt, stop=0.9, output_frequency=100):
			if self.constants.ki != self.constants.kw:
				print('--correcting thermal properties to be the same')
				self.constants.rho_w = self.constants.rho_i
				self.constants.cp_w = self.constants.cp_i
				self.constants.kw = self.constants.ki

			self.dt = dt
			self.model_time = 0
			self.set_boundaryconditions(top=True, bottom=True, sides='Reflect')
			self.num_iter = 0
			self.outputs.get_results(self, n=0)
			n = 1
			tmp = np.where(self.phi > 0)
			ff = min(tmp[0]) * self.dz
			strt = _timer_
			while ff <= stop * self.Lz:
				TErr, phiErr = np.inf, np.inf
				iter_k = 0
				while (TErr > self.Ttol) and (phiErr > self.phitol):
					T_last, phi_last = self.T.copy(), self.phi.copy()

					self.update_liquid_fraction(phi_last=phi_last)
					self.update_volume_averages()

					# constant in front of x-terms
					Cx = self.dt / (2 * self.rhoc[1:-1, 1:-1] * self.dx ** 2)
					# constant in front of z-terms
					Cz = self.dt / (2 * self.rhoc[1:-1, 1:-1] * self.dz ** 2)
					# temperature terms in z direction
					Tz = Cz * ((self.k[1:-1, 1:-1] + self.k[2:, 1:-1]) * (T_last[2:, 1:-1] - T_last[1:-1, 1:-1]) \
					           - (self.k[1:-1, 1:-1] + self.k[:-2, 1:-1]) * (T_last[1:-1, 1:-1] - T_last[:-2, 1:-1]))
					# temperature terms in x direction
					Tx = Cx * ((self.k[1:-1, 1:-1] + self.k[1:-1, 2:]) * (T_last[1:-1, 2:] - T_last[1:-1, 1:-1]) \
					           - (self.k[1:-1, 1:-1] + self.k[1:-1, :-2]) * (T_last[1:-1, 1:-1] - T_last[1:-1, :-2]))

					self.update_sources_sinks(phi_last=phi_last, T_last=T_last)

					self.T[1:-1, 1:-1] = T_last[1:-1, 1:-1] + Tx + Tz + self.Q * self.dt / self.rhoc[1:-1, 1:-1]
					self.apply_boundary_conditions(T=T_last)

					TErr = (abs(self.T[1:-1, 1:-1] - T_last[1:-1, 1:-1])).max()
					phiErr = (abs(self.phi[1:-1, 1:-1] - phi_last[1:-1, 1:-1])).max()

					iter_k += 1
					# kill statement when parameters won't allow solution to converge
					if iter_k > 1000:
						raise Exception('solution not converging')

				# outputs here
				self.num_iter = iter_k
				self.model_time = n * self.dt

				self.outputs.get_results(self, n=n)
				self.model_time = n * self.dt

				n += 1
				tmp = np.where(self.phi > 0)
				ff = min(tmp[0]) * self.dz
			self.run_time = _timer_.time() - strt
			self.stefan.solution(self, t=n * self.dt, T1=self.Tsurf, T0=self.Tbot)