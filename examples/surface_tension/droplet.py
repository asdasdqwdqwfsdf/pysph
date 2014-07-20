"""Deformation of a square droplet"""

import numpy

# Particle generator
from pysph.base.utils import get_particle_array
from pysph.base.kernels import CubicSpline, WendlandQuintic, Gaussian

# SPH Equations and Group
from pysph.sph.equation import Group

from pysph.sph.wc.viscosity import ClearyArtificialViscosity

from pysph.sph.wc.transport_velocity import SummationDensity, MomentumEquationPressureGradient,\
    SolidWallPressureBC, SolidWallNoSlipBC, ShepardFilteredVelocity, \
    StateEquation, MomentumEquationArtificialStress, MomentumEquationViscosity

from pysph.sph.surface_tension import ColorGradientUsingNumberDensity, \
    InterfaceCurvatureFromNumberDensity, ShadlooYildizSurfaceTensionForce

from pysph.sph.gas_dynamics.basic import ScaleSmoothingLength

# PySPH solver and application
from pysph.solver.application import Application
from pysph.solver.solver import Solver

# Integrators and Steppers
from pysph.sph.integrator_step import TransportVelocityStep
from pysph.sph.integrator import PECIntegrator

# Domain manager for periodic domains
from pysph.base.nnps import DomainManager

# problem parameters
dim = 2
domain_width = 1.0
domain_height = 1.0

# numerical constants
wavelength = 1.0
wavenumber = 2*numpy.pi/wavelength
rho0 = rho1 = 1000.0
rho2 = 1*rho1
U = 0.5
sigma = 1.0

# set factor1 to [0.5 ~ 1.0] to simulate a thick or thin
# interface. Larger values result in a thick interface.
factor1 = 0.8
factor2 = 1./factor1

# initial perturbation amplitude
psi0 = 0.03*domain_height

# discretization parameters
dx = dy = 0.0125
dxb2 = dyb2 = 0.5 * dx
volume = dx*dx
hdx = 1.3
h0 = hdx * dx
rho0 = 1000.0
c0 = 10.0
p0 = c0*c0*rho0
nu = 1.0/rho0

# time steps
tf = 3.0
dt_cfl = 0.25 * h0/( 1.1*c0 )
dt_viscous = 0.125 * h0**2/nu
dt_force = 1.0

dt = 0.9 * min(dt_cfl, dt_viscous, dt_force)

def create_particles(**kwargs):
    x, y = numpy.mgrid[ dxb2:domain_width:dx, dyb2:domain_height:dy ]
    x = x.ravel(); y = y.ravel()

    m = numpy.ones_like(x) * volume * rho0
    rho = numpy.ones_like(x) * rho0
    h = numpy.ones_like(x) * h0
    cs = numpy.ones_like(x) * c0

    # additional properties required for the fluid.
    additional_props = [
        # volume inverse or number density
        'V', 

        # color and gradients
        'color', 'cx', 'cy', 'cz', 'cx2', 'cy2', 'cz2',
        
        # discretized interface normals and dirac delta
        'nx', 'ny', 'nz', 'ddelta',

        # interface curvature
        'kappa',
        
        # filtered velocities
        'uf', 'vf', 'wf',
        
        # transport velocities
        'uhat', 'vhat', 'what', 'auhat', 'avhat', 'awhat', 
        
        # imposed accelerations on the solid wall
        'ax', 'ay', 'az', 'wij', 
       
        # velocity of magnitude squared
        'vmag2',
        
        ]

    # get the fluid particle array
    fluid = get_particle_array(
        name='fluid', x=x, y=y, h=h, m=m, rho=rho, cs=cs, 
        additional_props=additional_props)

    # set the color of the inner square
    for i in range(x.size):
        if ( (fluid.x[i] > 0.35) and (fluid.x[i] < 0.65) ):
            if ( (fluid.y[i] > 0.35) and (fluid.y[i] < 0.65) ):
                fluid.color[i] = 1.0
                
    # particle volume
    fluid.V[:] = 1./volume

    # set additional output arrays for the fluid
    fluid.add_output_arrays(['V', 'color', 'cx', 'cy', 'nx', 'ny', 'ddelta'])
    
    print "2D Square droplet deformation with %d fluid particles"%(
            fluid.get_number_of_particles())

    return [fluid,]
    
# domain for periodicity
domain = DomainManager(
    xmin=0, xmax=domain_width, ymin=0, ymax=domain_height,
    periodic_in_x=True, periodic_in_y=True)

# Create the application.
app = Application(domain=domain)

# Create the kernel
kernel = WendlandQuintic(dim=2)
#kernel = CubicSpline(dim=2)
#kernel = Gaussian(dim=2)

# Create the Integrator.
integrator = PECIntegrator( fluid=TransportVelocityStep() )

# create the equations
equations = [

    # We first compute the mass and number density of the fluid
    # phase. This is used in all force computations henceforth. The
    # number density (1/volume) is explicitly set for the solid phase
    # and this isn't modified for the simulation.
    Group(equations=[
            SummationDensity( dest='fluid', sources=['fluid'] )
            ] ),
    
    # Given the updated number density for the fluid, we can update
    # the fluid pressure. Additionally, we can compute the Shepard
    # Filtered velocity required for the no-penetration boundary
    # condition.
    Group(equations=[
            StateEquation(dest='fluid', sources=None, rho0=rho0, p0=p0),
            ] ),

    #################################################################
    # Begin Surface tension formulation
    #################################################################
    # Scale the smoothing lengths to determine the interface
    # quantities. The NNPS is updated after this group to get the new
    # list of neighbors.
    Group(equations=[
            ScaleSmoothingLength(dest='fluid', sources=None, factor=factor1)
            ], update_nnps=True ),

    # Compute the gradient of the color function with respect to the
    # new smoothing length. At the end of this Group, we will have the
    # interface normals and the discretized dirac delta function for
    # the fluid-fluid interface.
    Group(equations=[
            ColorGradientUsingNumberDensity(dest='fluid', sources=['fluid']),
            ], 
          ),

    # Compute the interface curvature using the modified smoothing
    # length and interface normals computed in the previous Group.
    Group(equations=[
            InterfaceCurvatureFromNumberDensity(dest='fluid', sources=['fluid']),
            ], ),

    # Now rescale the smoothing length to the original value for the
    # rest of the computations. Re-compute NNPS at the end of this
    # Group since the smoothing lengths are modified.
    Group(equations=[
            ScaleSmoothingLength(dest='fluid', sources=None, factor=factor2)
            ], update_nnps=True,
          ),
    #################################################################
    # End Surface tension formulation
    #################################################################

    # The main acceleration block
    Group(
        equations=[

            # Gradient of pressure for the fluid phase using the
            # number density formulation. No penetration boundary
            # condition using Adami et al's generalized wall boundary
            # condition. The extrapolated pressure and density on the
            # wall particles is used in the gradient of pressure to
            # simulate a repulsive force.
            MomentumEquationPressureGradient(
                dest='fluid', sources=['fluid'], pb=p0),

            # Artificial viscosity for the fluid phase.
            MomentumEquationViscosity(
                dest='fluid', sources=['fluid'], nu=nu),

            # Surface tension force for the SY11 formulation
            ShadlooYildizSurfaceTensionForce(dest='fluid', sources=None, sigma=sigma),

            # Artificial stress for the fluid phase
            MomentumEquationArtificialStress(dest='fluid', sources=['fluid']),
                                               
            ], )
    ]

# Create a solver.
solver = Solver(
    kernel=kernel, dim=dim, integrator=integrator,
    dt=dt, tf=tf, adaptive_timestep=False)

# Setup the application and solver.  This also generates the particles.
app.setup(solver=solver, equations=equations,
          particle_factory=create_particles)

app.run()