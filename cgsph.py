from collections import defaultdict, OrderedDict
from textwrap import dedent

###############################################################################
# `CubicSpline` class.
###############################################################################
class CubicSpline(object):
    def cython_code(self):
        code = dedent('''\
        double cdef CubicSplineKernel(double x, double y, double h):
            return 1.0
        ''')
        return dict(helper=code)


###############################################################################
# `Equation` class.
###############################################################################
class Equation(object):
    def __init__(self, dest, sources):
        self.dest = dest
        self.sources = sources
        
###############################################################################
# `Group` class.
###############################################################################
class Group(object):
    def __init__(self, equations):
        self.equations = equations
        
###############################################################################
# `Variable` class.
###############################################################################
class Variable(object):
    """Should be unique to each equation.
    """
    def __init__(self, type, name, default=None):
        self.type = type
        self.name = name
        self.default = default
        if default is not None:
            declare = 'cdef {0} {1} = {2}'.format(self.type, self.name, 
                                                self.default)
            initialize = '{0} = {1}'.format(self.name, self.default)
        else:
            declare = 'cdef {0} {1}'.format(self.type, self.name)
            initialize = ''
        self.declare = declare
        self.initialize = initialize
        
###############################################################################
# `Temporary` class.
###############################################################################
class Temporary(Variable):
    """Use for temporary variables.  Can be common between equations.
    """
    pass
    

###############################################################################
# `SummationDensity` class.
###############################################################################
class SummationDensity(Equation):
    def cython_code(self):
        variables = [Variable(type='double', name='rho_sum', default=0.0)]
        temp = [Temporary(type='double', name='hab', default=0.0)]
        arrays = ['s_h', 's_m', 's_x', 'd_h', 'd_x', 'd_rho']
        
        loop = dedent('''\
        hab = 0.5*(s_h[s_idx] + d_h[d_idx])
        rho_sum += s_m[s_idx]*KERNEL(d_x[d_idx], s_x[s_idx], hab)
        ''')
        post = dedent('''\
        d_rho[d_idx] = rho_sum
        ''')
        return dict(variables=variables, temporaries=temp, loop=loop, post=post,
                    arrays=arrays)
 
###############################################################################
# `Locator` class.
###############################################################################
class Locator(object):
    def initialize(self):
        raise NotImplementedError()
        
    def get_neighbors(self, d_idx, nbr_array):
        raise NotImplementedError()
        
    def cython_code(self):
        raise NotImplementedError
        
        
###############################################################################
# `AllPairLocator` class.
###############################################################################
class AllPairLocator(Locator):
    def initialize(self):
        self.len = len(self.s_x)
        
    def cython_code(self):
        helper = dedent('''\
        cdef class AllPairLocator:
            cdef long N
            cdef LongArray nbrs
            def __init__(self, s_x, s_h, d_x, d_h):
                self.N = len(s_x)
                self.nbrs = LongArray(self.N)
                cdef long i
                for i in range(self.N):
                    self.nbrs[i] = i
                
            def get_neighbors(long d_idx, LongArray nbr_array):
                nbr_array.resize(self.N)
                nbr_array.copy_values(self.nbrs)
        '''
        )
        setup = dedent('''\
        locator = AllPairLocator(s_x, s_h, d_x, d_h)
        ''')
        return dict(helper=helper, setup=setup)
        
###############################################################################
# `SourceCode` class.
###############################################################################
class SourceCode(object):
    def __init__(self):
        self.code = []
        self.level = 0
    
    def indent(self):
        self.level += 1
        
    def dedent(self):
        self.level -= 1
        
    def add(self, code):
        """Add the given code string to the source suitably indented based
        on the current indentation level.
        """
        self.code.append((code, self.level))
        
    def get(self):
        """Return a string of the code, properly indented."""
        src = [self._indented_code(c, l) for c, l in self.code]
        return '\n'.join(src)
        
    def _indented_code(self, code, level):
        indent = level*4*' '
        src = []
        for line in code.splitlines():
            src.append(indent + line)
        return '\n'.join(src)
    
###############################################################################
# `VariableNameClashError` class.
###############################################################################
class VariableNameClashError(Exception):
    pass

###############################################################################
def check_equations(equations):
    only_groups = [x for x in equations if isinstance(x, Group)]
    if len(only_groups) > 0 and len(only_groups) != len(equations):
        raise ValueError('All elements must be Groups if you use groups.')
    if len(only_groups) == 0:
        return [Group(equations)]
    else:
        return equations
        
###############################################################################
def get_code(obj, key):
    code = obj.cython_code()
    doc = '# From %s'%obj.__class__.__name__
    return [doc, code.get(key)] if key in code else []
    
###############################################################################
def define_particle_array_wrapper(particle_arrays):
    """Get the union of all particle arrays."""
    props = set()
    for array in particle_arrays:
        for name in array.properties.keys():
            props.add(name)
    props.difference_update(set(('tag', 'group', 'local', 'pid')))
    array_code = ', '.join(props)
    
    src = dedent('''\
    from pysph.base.particle_array cimport ParticleArray
    from pysph.base.particle_array import ParticleArray
    
    cdef class ParticleArrayWrapper:
        cdef public ParticleArray array
        cdef public LongArray tag, group
        cdef public IntArray local, pid
        cdef public DoubleArray {array_code}
        
        def __init__(self, pa):
            self.array = pa
            props = set(pa.properties.keys())
            props = props.union(['tag', 'group', 'local', 'pid'])
            for prop in props:
                setattr(self, prop, pa.get_carray(prop))
            
        cpdef long size(self):
            return self.array.get_number_of_particles()
            
            
    '''.format(array_code=array_code))
    return dict(code=src)

###############################################################################
# `SPHEval` class.
###############################################################################
class SPHEval(object):
    def __init__(self, particle_arrays, equations, locator, kernel):
        self.particle_arrays = particle_arrays
        self.equation_groups = check_equations(equations)
        self.locator = locator
        self.kernel = kernel
        self.groups = [self._make_group(g) for g in self.equation_groups]
        #self.generate()
        
    def _make_group(self, group):
        equations = group.equations
        dest_list = []
        for equation in equations:
            dest = equation.dest
            if dest not in dest_list:
                dest_list.append(dest)
        
        dests = OrderedDict()
        for dest in dest_list:
            sources = defaultdict(list)
            for equation in equations:
                for src in equation.sources:
                    sources[src].append(equation)
            dests[dest] = sources
            
        return dests
                
    def _get_helpers(self):
        helpers = []
        helpers.extend(['from pysph.base.carray cimport DoubleArray, LongArray, IntArray, UIntArray',
                        'from pysph.base.carray import DoubleArray, LongArray, IntArray, UIntArray',
                        ''
                        ])
        helpers.extend(get_code(self.kernel, 'helper'))
        
        for group in self.equation_groups:
            for eq in group.equations:
                helpers.extend(get_code(eq, 'helper'))
                
        helpers.extend(get_code(self.locator, 'helper'))
        
        helpers.append(define_particle_array_wrapper(self.particle_arrays).get('code'))
        return '\n'.join(helpers)
        
    def _check_and_get_variables(self):
        vars = []
        temps = []
        var_names = defaultdict(list)
        tmp_names = defaultdict(list)
        tmp_declare = defaultdict(list)
        equations = []
        for g in self.equation_groups:
            equations.extend(g.equations)
            
        for equation in equations:
            eq_name = equation.__class__.__name__
            code = equation.cython_code()
            v = code.get('variables', [])
            vars.extend(v)
            names = [x.name for x in v]
            for name in names:
                var_names[name].append(eq_name)
            
            t = code.get('temporaries', [])
            temps.extend(t)
            names = [(x.name, x.declare) for x in t]
            for name, declare in names:
                tmp_names[name].append(eq_name)
                tmp_declare[name].append(declare)

        for name, eqs in var_names.iteritems():
            if len(eqs) > 1:
                msg = 'Variable %s defined in %s.'%(name, eqs)
                raise VariableNameClashError(msg)
                
        for name, eqs in tmp_names.iteritems():
            if name in var_names:
                msg = 'Temporary %s in equation %s also defined as variable '\
                      'in %s'%(name, eqs, var_names[name])
                raise VariableNameClashError(msg)
                
        for name, eqs in tmp_names.iteritems():
            if len(eqs) > 1:
                declares = tmp_declare[name]
                if not all(map(lambda v: v == declares[0], declares)):
                    msg = "Temporary declarations for %s in %s differ"%\
                            (name, eqs)
                    raise VariableNameClashError(msg)
        return vars, temps
        
    def _get_variable_declarations(self, vars, tmps):
        decl = {}
        for var in vars:
            decl[var.declare] = None
        for var in tmps:
            decl[var.declare] = None
        return '\n'.join(decl.keys())
        
    def _get_array_declarations(self):
        equations = []
        for g in self.equation_groups:
            equations.extend(g.equations)
            
        decl = {}
        for eq in equations:
            code = eq.cython_code()
            for array in code.get('arrays'):
                src = 'cdef double* %s'%array
                decl[src] = None
        return '\n'.join(decl.keys())
                
    def _get_initialization(self, equations):
        init = {}
        for equation in equations:
            code = equation.cython_code()
            vars = code.get('variables')
            tmps = code.get('temporaries')
            for var in vars:
                init[var.initialize] = None
            for var in tmps:
                init[var.initialize] = None
            
        return '\n'.join(init.keys())
        
    def _get_equation_loop(self, equation):
        code = equation.cython_code().get('loop')
        kernel = self.kernel.__class__.__name__ + 'Kernel'
        gradient = self.kernel.__class__.__name__ + 'Gradient'
        code = code.replace('KERNEL', kernel).replace('GRADIENT', gradient)
        return code
        
    def _get_dest_array_setup(self, dest_name, sources):
        eqs = []
        for eq in sources.values():
            eqs.extend(eq)
        eqs = set(eqs)
        names = ['d_x', 'd_y', 'd_z', 'd_h'] # Needed for locator.
        names += [arr for e in eqs for arr in e.cython_code().get('arrays') 
                    if arr.startswith('d_')]
        names = set(names)
        lines = ['NP_DEST = self.%s.size()'%dest_name]
        lines += ['%s = self.%s.%s.get_data_ptr()'%(n, dest_name, n[2:]) 
                 for n in names]
        return '\n'.join(lines)
        
    def _get_src_array_setup(self, src_name, eqs):
        names = ['s_x', 's_y', 's_z', 's_h'] # Needed for locator.
        names += [arr for e in eqs for arr in e.cython_code().get('arrays') 
                    if arr.startswith('s_')]
        names = set(names)
        lines = ['%s = self.%s.%s.get_data_ptr()'%(n, src_name, n[2:]) 
                 for n in names]
        return '\n'.join(lines)
        
        
    def _get_body(self):
        vars, tmps = self._check_and_get_variables()
        
        src = SourceCode()
        level = 0
        
        parrays = [pa.name for pa in self.particle_arrays]
        pa_names = ', '.join(parrays)    
        code = dedent('''\
        cdef class SPHCalc:

            cdef public ParticleArrayWrapper {pa_names}
    
            def __init__(self, *particle_arrays):
                for pa in particle_arrays:
                    name = pa.name
                    setattr(self, name, ParticleArrayWrapper(pa))
            
            cpdef void compute(self):
                cdef public long s_idx, d_idx, NP_SRC, NP_DEST
                cdef public LongArray nbrs
            
        '''.format(pa_names=pa_names))
        src.add(code)
        src.indent()
        src.indent()
        
        src.add(self._get_array_declarations())
        src.add(self._get_variable_declarations(vars, tmps))
        
        for g_idx, group in enumerate(self.groups):
            src.add('# Group %d.\n'%g_idx)
            for dest, sources in group.iteritems():
                code = dedent('''\
                        # Destination %s.
                        '''%dest)
                src.add(code)
                src.add(self._get_dest_array_setup(dest, sources))
                for source, equations in sources.iteritems():
                    code = dedent('''\
                            # Source %s.
                            '''%source)
                    src.add(code)
                    src.add(self._get_src_array_setup(source, equations))
                    
                    src.add(self.locator.cython_code().get('setup'))
                    code = dedent('''\
                            for d_idx in range(NP_DEST):
                            ''')
                    src.add(code)
                    src.indent()
                    
                    # Initialize the variables.
                    init = self._get_initialization(equations)
                    src.add(init)
                    
                    code = dedent('''\
                            locator.get_neighbors(d_idx, nbrs)
                            for nbr_idx in range(len(nbrs)):
                                s_idx = nbrs[nbr_idx]
                            ''')
                    src.add(code)
                    src.indent()
                    for equation in equations:
                        src.add('# Equation %s'%equation.__class__.__name__)
                        src.add(self._get_equation_loop(equation))
                    src.dedent()
                    for equation in equations:
                        code = equation.cython_code()
                        src.add(code.get('post'))
                    src.dedent()
                    src.add('# Source %s done.\n'%source)
                src.add('# Destination %s done.\n'%dest)
            src.add('# Group %d done.'%g_idx)
                    
        return src.get()
        
    def generate(self, fp):
        code = self._get_helpers()
        code += self._get_body()
        fp.write(code)
        
    def compute(self):
        pass
