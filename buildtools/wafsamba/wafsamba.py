# a waf tool to add autoconf-like macros to the configure section
# and for SAMBA_ macros for building libraries, binaries etc

import os, sys, re, shutil, fnmatch
from waflib import Build, Options, Task, Utils, TaskGen, Logs, Context, Errors
from waflib.Configure import conf
from waflib.Logs import debug
from samba_utils import SUBST_VARS_RECURSIVE
TaskGen.task_gen.apply_verif = Utils.nada

# bring in the other samba modules
from samba_utils import *
from samba_utils import symlink
from samba_version import *
from samba_autoconf import *
from samba_patterns import *
from samba_pidl import *
from samba_autoproto import *
from samba_python import *
from samba_perl import *
from samba_deps import *
from samba_bundled import *
from samba_third_party import *
import samba_cross
import samba_install
import samba_conftests
import samba_abi
import samba_headers
import generic_cc
import samba_dist
import samba_wildcard
import symbols
import pkgconfig
import configure_file
import samba_waf18

LIB_PATH="shared"

os.environ['PYTHONUNBUFFERED'] = '1'

if Context.HEXVERSION not in (0x2001600,):
    Logs.error('''
Please use the version of waf that comes with Samba, not
a system installed version. See http://wiki.samba.org/index.php/Waf
for details.

Alternatively, please run ./configure and make as usual. That will
call the right version of waf.''')
    sys.exit(1)

@conf
def SAMBA_BUILD_ENV(conf):
    '''create the samba build environment'''
    conf.env.BUILD_DIRECTORY = conf.bldnode.abspath()
    mkdir_p(os.path.join(conf.env.BUILD_DIRECTORY, LIB_PATH))
    mkdir_p(os.path.join(conf.env.BUILD_DIRECTORY, LIB_PATH, "private"))
    mkdir_p(os.path.join(conf.env.BUILD_DIRECTORY, "modules"))
    mkdir_p(os.path.join(conf.env.BUILD_DIRECTORY, 'python/samba/dcerpc'))
    # this allows all of the bin/shared and bin/python targets
    # to be expressed in terms of build directory paths
    mkdir_p(os.path.join(conf.env.BUILD_DIRECTORY, 'default'))
    for (source, target) in [('shared', 'shared'), ('modules', 'modules'), ('python', 'python')]:
        link_target = os.path.join(conf.env.BUILD_DIRECTORY, 'default/' + target)
        if not os.path.lexists(link_target):
            symlink('../' + source, link_target)

    # get perl to put the blib files in the build directory
    blib_bld = os.path.join(conf.env.BUILD_DIRECTORY, 'default/pidl/blib')
    blib_src = os.path.join(conf.srcnode.abspath(), 'pidl/blib')
    mkdir_p(blib_bld + '/man1')
    mkdir_p(blib_bld + '/man3')
    if os.path.islink(blib_src):
        os.unlink(blib_src)
    elif os.path.exists(blib_src):
        shutil.rmtree(blib_src)


def ADD_INIT_FUNCTION(bld, subsystem, target, init_function):
    '''add an init_function to the list for a subsystem'''
    if init_function is None:
        return
    bld.ASSERT(subsystem is not None, "You must specify a subsystem for init_function '%s'" % init_function)
    cache = LOCAL_CACHE(bld, 'INIT_FUNCTIONS')
    if not subsystem in cache:
        cache[subsystem] = []
    cache[subsystem].append( { 'TARGET':target, 'INIT_FUNCTION':init_function } )
Build.BuildContext.ADD_INIT_FUNCTION = ADD_INIT_FUNCTION


def generate_empty_file(task):
    task.outputs[0].write('')
    return 0

#################################################################
def SAMBA_LIBRARY(bld, libname, source,
                  deps='',
                  public_deps='',
                  includes='',
                  public_headers=None,
                  public_headers_install=True,
                  private_headers=None,
                  header_path=None,
                  pc_files=None,
                  vnum=None,
                  soname=None,
                  cflags='',
                  cflags_end=None,
                  ldflags='',
                  external_library=False,
                  realname=None,
                  keep_underscore=False,
                  autoproto=None,
                  autoproto_extra_source='',
                  group='main',
                  depends_on='',
                  local_include=True,
                  global_include=True,
                  vars=None,
                  subdir=None,
                  install_path=None,
                  install=True,
                  pyembed=False,
                  pyext=False,
                  target_type='LIBRARY',
                  bundled_extension=False,
                  bundled_name=None,
                  link_name=None,
                  abi_directory=None,
                  abi_match=None,
                  hide_symbols=False,
                  manpages=None,
                  private_library=False,
                  grouping_library=False,
                  allow_undefined_symbols=False,
                  allow_warnings=False,
                  enabled=True):
    '''define a Samba library'''

    if private_library and public_headers:
        raise Errors.WafError("private library '%s' must not have public header files" %
                             libname)

    if LIB_MUST_BE_PRIVATE(bld, libname):
        private_library = True

    if not enabled:
        SET_TARGET_TYPE(bld, libname, 'DISABLED')
        return

    source = bld.EXPAND_VARIABLES(source, vars=vars)
    if subdir:
        source = bld.SUBDIR(subdir, source)

    # remember empty libraries, so we can strip the dependencies
    if ((source == '') or (source == [])):
        if deps == '' and public_deps == '':
            SET_TARGET_TYPE(bld, libname, 'EMPTY')
            return
        empty_c = libname + '.empty.c'
        bld.SAMBA_GENERATOR('%s_empty_c' % libname,
                            rule=generate_empty_file,
                            target=empty_c)
        source=empty_c

    if BUILTIN_LIBRARY(bld, libname):
        obj_target = libname
    else:
        obj_target = libname + '.objlist'

    if group == 'libraries':
        subsystem_group = 'main'
    else:
        subsystem_group = group

    # first create a target for building the object files for this library
    # by separating in this way, we avoid recompiling the C files
    # separately for the install library and the build library
    bld.SAMBA_SUBSYSTEM(obj_target,
                        source         = source,
                        deps           = deps,
                        public_deps    = public_deps,
                        includes       = includes,
                        public_headers = public_headers,
                        public_headers_install = public_headers_install,
                        private_headers= private_headers,
                        header_path    = header_path,
                        cflags         = cflags,
                        cflags_end     = cflags_end,
                        group          = subsystem_group,
                        autoproto      = autoproto,
                        autoproto_extra_source=autoproto_extra_source,
                        depends_on     = depends_on,
                        hide_symbols   = hide_symbols,
                        allow_warnings = allow_warnings,
                        pyembed        = pyembed,
                        pyext          = pyext,
                        local_include  = local_include,
                        global_include = global_include)

    if BUILTIN_LIBRARY(bld, libname):
        return

    if not SET_TARGET_TYPE(bld, libname, target_type):
        return

    # the library itself will depend on that object target
    deps += ' ' + public_deps
    deps = TO_LIST(deps)
    deps.append(obj_target)

    realname = bld.map_shlib_extension(realname, python=(target_type=='PYTHON'))
    link_name = bld.map_shlib_extension(link_name, python=(target_type=='PYTHON'))

    # we don't want any public libraries without version numbers
    if (not private_library and target_type != 'PYTHON' and not realname):
        if vnum is None and soname is None:
            raise Errors.WafError("public library '%s' must have a vnum" %
                    libname)
        if pc_files is None:
            raise Errors.WafError("public library '%s' must have pkg-config file" %
                       libname)
        if public_headers is None:
            raise Errors.WafError("public library '%s' must have header files" %
                       libname)

    if bundled_name is not None:
        pass
    elif target_type == 'PYTHON' or realname or not private_library:
        if keep_underscore:
            bundled_name = libname
        else:
            bundled_name = libname.replace('_', '-')
    else:
        assert (private_library == True and realname is None)
        if abi_directory or vnum or soname:
            bundled_extension=True
        bundled_name = PRIVATE_NAME(bld, libname.replace('_', '-'),
                                    bundled_extension, private_library)

    ldflags = TO_LIST(ldflags)
    if bld.env['ENABLE_RELRO'] is True:
        ldflags.extend(TO_LIST('-Wl,-z,relro,-z,now'))

    features = 'c cshlib symlink_lib install_lib'
    if pyext:
        features += ' pyext'
    if pyembed:
        features += ' pyembed'

    if abi_directory:
        features += ' abi_check'

    if pyembed and bld.env['PYTHON_SO_ABI_FLAG']:
        # For ABI checking, we don't care about the Python version.
        # Remove the Python ABI tag (e.g. ".cpython-35m")
        abi_flag = bld.env['PYTHON_SO_ABI_FLAG']
        replacement = ''
        version_libname = libname.replace(abi_flag, replacement)
    else:
        version_libname = libname

    vscript = None
    if bld.env.HAVE_LD_VERSION_SCRIPT:
        if private_library:
            version = "%s_%s" % (Context.g_module.APPNAME, Context.g_module.VERSION)
        elif vnum:
            version = "%s_%s" % (libname, vnum)
        else:
            version = None
        if version:
            vscript = "%s.vscript" % libname
            bld.ABI_VSCRIPT(version_libname, abi_directory, version, vscript,
                            abi_match)
            fullname = apply_pattern(bundled_name, bld.env.cshlib_PATTERN)
            fullpath = bld.path.find_or_declare(fullname)
            vscriptpath = bld.path.find_or_declare(vscript)
            if not fullpath:
                raise Errors.WafError("unable to find fullpath for %s" % fullname)
            if not vscriptpath:
                raise Errors.WafError("unable to find vscript path for %s" % vscript)
            bld.add_manual_dependency(fullpath, vscriptpath)
            if bld.is_install:
                # also make the .inst file depend on the vscript
                instname = apply_pattern(bundled_name + '.inst', bld.env.cshlib_PATTERN)
                bld.add_manual_dependency(bld.path.find_or_declare(instname), bld.path.find_or_declare(vscript))
            vscript = os.path.join(bld.path.abspath(bld.env), vscript)

    bld.SET_BUILD_GROUP(group)
    t = bld(
        features        = features,
        source          = [],
        target          = bundled_name,
        depends_on      = depends_on,
        samba_ldflags   = ldflags,
        samba_deps      = deps,
        samba_includes  = includes,
        version_script  = vscript,
        version_libname = version_libname,
        local_include   = local_include,
        global_include  = global_include,
        vnum            = vnum,
        soname          = soname,
        install_path    = None,
        samba_inst_path = install_path,
        name            = libname,
        samba_realname  = realname,
        samba_install   = install,
        abi_directory   = "%s/%s" % (bld.path.abspath(), abi_directory),
        abi_match       = abi_match,
        private_library = private_library,
        grouping_library=grouping_library,
        allow_undefined_symbols=allow_undefined_symbols
        )

    if realname and not link_name:
        link_name = 'shared/%s' % realname

    if link_name:
        if 'waflib.extras.compat15' in sys.modules:
            link_name = 'default/' + link_name
        t.link_name = link_name

    if pc_files is not None and not private_library:
        if pyembed:
            bld.PKG_CONFIG_FILES(pc_files, vnum=vnum, extra_name=bld.env['PYTHON_SO_ABI_FLAG'])
        else:
            bld.PKG_CONFIG_FILES(pc_files, vnum=vnum)

    if (manpages is not None and 'XSLTPROC_MANPAGES' in bld.env and
        bld.env['XSLTPROC_MANPAGES']):
        bld.MANPAGES(manpages, install)


Build.BuildContext.SAMBA_LIBRARY = SAMBA_LIBRARY


#################################################################
def SAMBA_BINARY(bld, binname, source,
                 deps='',
                 includes='',
                 public_headers=None,
                 private_headers=None,
                 header_path=None,
                 modules=None,
                 ldflags=None,
                 cflags='',
                 cflags_end=None,
                 autoproto=None,
                 use_hostcc=False,
                 use_global_deps=True,
                 compiler=None,
                 group='main',
                 manpages=None,
                 local_include=True,
                 global_include=True,
                 subsystem_name=None,
                 allow_warnings=False,
                 pyembed=False,
                 vars=None,
                 subdir=None,
                 install=True,
                 install_path=None,
                 enabled=True,
                 fuzzer=False,
                 for_selftest=False):
    '''define a Samba binary'''

    if for_selftest:
        install=False
        if not bld.CONFIG_GET('ENABLE_SELFTEST'):
            enabled=False

    if not enabled:
        SET_TARGET_TYPE(bld, binname, 'DISABLED')
        return

    # Fuzzing builds do not build normal binaries
    # however we must build asn1compile etc

    if not use_hostcc and bld.env.enable_fuzzing != fuzzer:
        SET_TARGET_TYPE(bld, binname, 'DISABLED')
        return

    if fuzzer:
        install = False
        if ldflags is None:
            ldflags = bld.env['FUZZ_TARGET_LDFLAGS']

    if not SET_TARGET_TYPE(bld, binname, 'BINARY'):
        return

    features = 'c cprogram symlink_bin install_bin'
    if pyembed:
        features += ' pyembed'

    obj_target = binname + '.objlist'

    source = bld.EXPAND_VARIABLES(source, vars=vars)
    if subdir:
        source = bld.SUBDIR(subdir, source)
    source = unique_list(TO_LIST(source))

    if group == 'binaries':
        subsystem_group = 'main'
    elif group == 'build_compilers':
        subsystem_group = 'compiler_libraries'
    else:
        subsystem_group = group

    # only specify PIE flags for binaries
    pie_cflags = TO_LIST(cflags)
    pie_ldflags = TO_LIST(ldflags)
    if bld.env['ENABLE_PIE'] is True:
        pie_cflags.extend(TO_LIST('-fPIE'))
        pie_ldflags.extend(TO_LIST('-pie'))
    if bld.env['ENABLE_RELRO'] is True:
        pie_ldflags.extend(TO_LIST('-Wl,-z,relro,-z,now'))

    # first create a target for building the object files for this binary
    # by separating in this way, we avoid recompiling the C files
    # separately for the install binary and the build binary
    bld.SAMBA_SUBSYSTEM(obj_target,
                        source         = source,
                        deps           = deps,
                        includes       = includes,
                        cflags         = pie_cflags,
                        cflags_end     = cflags_end,
                        group          = subsystem_group,
                        autoproto      = autoproto,
                        subsystem_name = subsystem_name,
                        local_include  = local_include,
                        global_include = global_include,
                        use_hostcc     = use_hostcc,
                        pyext          = pyembed,
                        allow_warnings = allow_warnings,
                        use_global_deps= use_global_deps)

    bld.SET_BUILD_GROUP(group)

    # the binary itself will depend on that object target
    deps = TO_LIST(deps)
    deps.append(obj_target)

    t = bld(
        features       = features,
        source         = [],
        target         = binname,
        samba_deps     = deps,
        samba_includes = includes,
        local_include  = local_include,
        global_include = global_include,
        samba_modules  = modules,
        top            = True,
        samba_subsystem= subsystem_name,
        install_path   = None,
        samba_inst_path= install_path,
        samba_install  = install,
        samba_ldflags  = pie_ldflags
        )

    if manpages is not None and 'XSLTPROC_MANPAGES' in bld.env and bld.env['XSLTPROC_MANPAGES']:
        bld.MANPAGES(manpages, install)

Build.BuildContext.SAMBA_BINARY = SAMBA_BINARY


#################################################################
def SAMBA_MODULE(bld, modname, source,
                 deps='',
                 includes='',
                 subsystem=None,
                 init_function=None,
                 module_init_name='samba_init_module',
                 autoproto=None,
                 autoproto_extra_source='',
                 cflags='',
                 cflags_end=None,
                 internal_module=True,
                 local_include=True,
                 global_include=True,
                 vars=None,
                 subdir=None,
                 enabled=True,
                 pyembed=False,
                 manpages=None,
                 allow_undefined_symbols=False,
                 allow_warnings=False,
                 install=True
                 ):
    '''define a Samba module.'''

    bld.ASSERT(subsystem, "You must specify a subsystem for SAMBA_MODULE(%s)" % modname)

    source = bld.EXPAND_VARIABLES(source, vars=vars)
    if subdir:
        source = bld.SUBDIR(subdir, source)

    if internal_module or BUILTIN_LIBRARY(bld, modname):
        # Do not create modules for disabled subsystems
        if GET_TARGET_TYPE(bld, subsystem) == 'DISABLED':
            return
        bld.SAMBA_SUBSYSTEM(modname, source,
                    deps=deps,
                    includes=includes,
                    autoproto=autoproto,
                    autoproto_extra_source=autoproto_extra_source,
                    cflags=cflags,
                    cflags_end=cflags_end,
                    local_include=local_include,
                    global_include=global_include,
                    allow_warnings=allow_warnings,
                    enabled=enabled)

        bld.ADD_INIT_FUNCTION(subsystem, modname, init_function)
        return

    if not enabled:
        SET_TARGET_TYPE(bld, modname, 'DISABLED')
        return

    # Do not create modules for disabled subsystems
    if GET_TARGET_TYPE(bld, subsystem) == 'DISABLED':
        return

    realname = modname
    deps += ' ' + subsystem
    while realname.startswith("lib"+subsystem+"_"):
        realname = realname[len("lib"+subsystem+"_"):]
    while realname.startswith(subsystem+"_"):
        realname = realname[len(subsystem+"_"):]

    build_name = "%s_module_%s" % (subsystem, realname)

    realname = bld.make_libname(realname)
    while realname.startswith("lib"):
        realname = realname[len("lib"):]

    build_link_name = "modules/%s/%s" % (subsystem, realname)

    if init_function:
        cflags += " -D%s=%s" % (init_function, module_init_name)

    bld.SAMBA_LIBRARY(modname,
                      source,
                      deps=deps,
                      includes=includes,
                      cflags=cflags,
                      cflags_end=cflags_end,
                      realname = realname,
                      autoproto = autoproto,
                      local_include=local_include,
                      global_include=global_include,
                      vars=vars,
                      bundled_name=build_name,
                      link_name=build_link_name,
                      install_path="${MODULESDIR}/%s" % subsystem,
                      pyembed=pyembed,
                      manpages=manpages,
                      allow_undefined_symbols=allow_undefined_symbols,
                      allow_warnings=allow_warnings,
                      install=install
                      )


Build.BuildContext.SAMBA_MODULE = SAMBA_MODULE


#################################################################
def SAMBA_SUBSYSTEM(bld, modname, source,
                    deps='',
                    public_deps='',
                    includes='',
                    public_headers=None,
                    public_headers_install=True,
                    private_headers=None,
                    header_path=None,
                    cflags='',
                    cflags_end=None,
                    group='main',
                    init_function_sentinel=None,
                    autoproto=None,
                    autoproto_extra_source='',
                    depends_on='',
                    local_include=True,
                    local_include_first=True,
                    global_include=True,
                    subsystem_name=None,
                    enabled=True,
                    use_hostcc=False,
                    use_global_deps=True,
                    vars=None,
                    subdir=None,
                    hide_symbols=False,
                    allow_warnings=False,
                    pyext=False,
                    pyembed=False):
    '''define a Samba subsystem'''

    if not enabled:
        SET_TARGET_TYPE(bld, modname, 'DISABLED')
        return

    # remember empty subsystems, so we can strip the dependencies
    if ((source == '') or (source == [])):
        if deps == '' and public_deps == '':
            SET_TARGET_TYPE(bld, modname, 'EMPTY')
            return
        empty_c = modname + '.empty.c'
        bld.SAMBA_GENERATOR('%s_empty_c' % modname,
                            rule=generate_empty_file,
                            target=empty_c)
        source=empty_c

    if not SET_TARGET_TYPE(bld, modname, 'SUBSYSTEM'):
        return

    source = bld.EXPAND_VARIABLES(source, vars=vars)
    if subdir:
        source = bld.SUBDIR(subdir, source)
    source = unique_list(TO_LIST(source))

    deps += ' ' + public_deps

    bld.SET_BUILD_GROUP(group)

    features = 'c'
    if pyext:
        features += ' pyext'
    if pyembed:
        features += ' pyembed'

    t = bld(
        features       = features,
        source         = source,
        target         = modname,
        samba_cflags   = CURRENT_CFLAGS(bld, modname, cflags,
                                        allow_warnings=allow_warnings,
                                        use_hostcc=use_hostcc,
                                        hide_symbols=hide_symbols),
        depends_on     = depends_on,
        samba_deps     = TO_LIST(deps),
        samba_includes = includes,
        local_include  = local_include,
        local_include_first  = local_include_first,
        global_include = global_include,
        samba_subsystem= subsystem_name,
        samba_use_hostcc = use_hostcc,
        samba_use_global_deps = use_global_deps,
        )

    if cflags_end is not None:
        t.samba_cflags.extend(TO_LIST(cflags_end))

    if autoproto is not None:
        bld.SAMBA_AUTOPROTO(autoproto, source + TO_LIST(autoproto_extra_source))
    if public_headers is not None:
        bld.PUBLIC_HEADERS(public_headers, header_path=header_path,
                           public_headers_install=public_headers_install)
    return t


Build.BuildContext.SAMBA_SUBSYSTEM = SAMBA_SUBSYSTEM


def SAMBA_GENERATOR(bld, name, rule, source='', target='',
                    group='generators', enabled=True,
                    public_headers=None,
                    public_headers_install=True,
                    private_headers=None,
                    header_path=None,
                    vars=None,
                    dep_vars=[],
                    always=False):
    '''A generic source generator target'''

    if not SET_TARGET_TYPE(bld, name, 'GENERATOR'):
        return

    if not enabled:
        return

    dep_vars.append('ruledeps')
    dep_vars.append('SAMBA_GENERATOR_VARS')

    bld.SET_BUILD_GROUP(group)
    t = bld(
        rule=rule,
        source=bld.EXPAND_VARIABLES(source, vars=vars),
        target=target,
        shell=isinstance(rule, str),
        update_outputs=True,
        before='c',
        ext_out='.c',
        samba_type='GENERATOR',
        dep_vars = dep_vars,
        name=name)

    if vars is None:
        vars = {}
    t.env.SAMBA_GENERATOR_VARS = vars

    if always:
        t.always = True

    if public_headers is not None:
        bld.PUBLIC_HEADERS(public_headers, header_path=header_path,
                           public_headers_install=public_headers_install)
    return t
Build.BuildContext.SAMBA_GENERATOR = SAMBA_GENERATOR



@Utils.run_once
def SETUP_BUILD_GROUPS(bld):
    '''setup build groups used to ensure that the different build
    phases happen consecutively'''
    bld.p_ln = bld.srcnode # we do want to see all targets!
    bld.env['USING_BUILD_GROUPS'] = True
    bld.add_group('setup')
    bld.add_group('generators')
    bld.add_group('hostcc_base_build_source')
    bld.add_group('hostcc_base_build_main')
    bld.add_group('hostcc_build_source')
    bld.add_group('hostcc_build_main')
    bld.add_group('vscripts')
    bld.add_group('base_libraries')
    bld.add_group('build_source')
    bld.add_group('prototypes')
    bld.add_group('headers')
    bld.add_group('main')
    bld.add_group('symbolcheck')
    bld.add_group('syslibcheck')
    bld.add_group('final')
Build.BuildContext.SETUP_BUILD_GROUPS = SETUP_BUILD_GROUPS


def SET_BUILD_GROUP(bld, group):
    '''set the current build group'''
    if not 'USING_BUILD_GROUPS' in bld.env:
        return
    bld.set_group(group)
Build.BuildContext.SET_BUILD_GROUP = SET_BUILD_GROUP



def SAMBA_SCRIPT(bld, name, pattern, installdir, installname=None):
    '''used to copy scripts from the source tree into the build directory
       for use by selftest'''

    source = bld.path.ant_glob(pattern, flat=True)

    bld.SET_BUILD_GROUP('build_source')
    for s in TO_LIST(source):
        iname = s
        if installname is not None:
            iname = installname
        target = os.path.join(installdir, iname)
        tgtdir = os.path.dirname(os.path.join(bld.srcnode.abspath(bld.env), '..', target))
        mkdir_p(tgtdir)
        link_src = os.path.normpath(os.path.join(bld.path.abspath(), s))
        link_dst = os.path.join(tgtdir, os.path.basename(iname))
        if os.path.islink(link_dst) and os.readlink(link_dst) == link_src:
            continue
        if os.path.islink(link_dst):
            os.unlink(link_dst)
        Logs.info("symlink: %s -> %s/%s" % (s, installdir, iname))
        symlink(link_src, link_dst)
Build.BuildContext.SAMBA_SCRIPT = SAMBA_SCRIPT


def copy_and_fix_python_path(task):
    pattern='sys.path.insert(0, "bin/python")'
    if task.env["PYTHONARCHDIR"] in sys.path and task.env["PYTHONDIR"] in sys.path:
        replacement = ""
    elif task.env["PYTHONARCHDIR"] == task.env["PYTHONDIR"]:
        replacement="""sys.path.insert(0, "%s")""" % task.env["PYTHONDIR"]
    else:
        replacement="""sys.path.insert(0, "%s")
sys.path.insert(1, "%s")""" % (task.env["PYTHONARCHDIR"], task.env["PYTHONDIR"])

    if task.env["PYTHON"][0].startswith("/"):
        replacement_shebang = "#!%s\n" % task.env["PYTHON"][0]
    else:
        replacement_shebang = "#!/usr/bin/env %s\n" % task.env["PYTHON"][0]

    installed_location=task.outputs[0].bldpath(task.env)
    source_file = open(task.inputs[0].srcpath(task.env))
    installed_file = open(installed_location, 'w')
    lineno = 0
    for line in source_file:
        newline = line
        if (lineno == 0 and
                line[:2] == "#!"):
            newline = replacement_shebang
        elif pattern in line:
            newline = line.replace(pattern, replacement)
        installed_file.write(newline)
        lineno = lineno + 1
    installed_file.close()
    os.chmod(installed_location, 0o755)
    return 0

def copy_and_fix_perl_path(task):
    pattern='use lib "$RealBin/lib";'

    replacement = ""
    if not task.env["PERL_LIB_INSTALL_DIR"] in task.env["PERL_INC"]:
         replacement = 'use lib "%s";' % task.env["PERL_LIB_INSTALL_DIR"]

    if task.env["PERL"][0] == "/":
        replacement_shebang = "#!%s\n" % task.env["PERL"]
    else:
        replacement_shebang = "#!/usr/bin/env %s\n" % task.env["PERL"]

    installed_location=task.outputs[0].bldpath(task.env)
    source_file = open(task.inputs[0].srcpath(task.env))
    installed_file = open(installed_location, 'w')
    lineno = 0
    for line in source_file:
        newline = line
        if lineno == 0 and task.env["PERL_SPECIFIED"] == True and line[:2] == "#!":
            newline = replacement_shebang
        elif pattern in line:
            newline = line.replace(pattern, replacement)
        installed_file.write(newline)
        lineno = lineno + 1
    installed_file.close()
    os.chmod(installed_location, 0o755)
    return 0


def install_file(bld, destdir, file, chmod=MODE_644, flat=False,
                 python_fixup=False, perl_fixup=False,
                 destname=None, base_name=None):
    '''install a file'''
    if not isinstance(file, str):
        file = file.abspath()
    destdir = bld.EXPAND_VARIABLES(destdir)
    if not destname:
        destname = file
        if flat:
            destname = os.path.basename(destname)
    dest = os.path.join(destdir, destname)
    if python_fixup:
        # fix the path python will use to find Samba modules
        inst_file = file + '.inst'
        bld.SAMBA_GENERATOR('python_%s' % destname,
                            rule=copy_and_fix_python_path,
                            dep_vars=["PYTHON","PYTHON_SPECIFIED","PYTHONDIR","PYTHONARCHDIR"],
                            source=file,
                            target=inst_file)
        file = inst_file
    if perl_fixup:
        # fix the path perl will use to find Samba modules
        inst_file = file + '.inst'
        bld.SAMBA_GENERATOR('perl_%s' % destname,
                            rule=copy_and_fix_perl_path,
                            dep_vars=["PERL","PERL_SPECIFIED","PERL_LIB_INSTALL_DIR"],
                            source=file,
                            target=inst_file)
        file = inst_file
    if base_name:
        file = os.path.join(base_name, file)
    bld.install_as(dest, file, chmod=chmod)


def INSTALL_FILES(bld, destdir, files, chmod=MODE_644, flat=False,
                  python_fixup=False, perl_fixup=False,
                  destname=None, base_name=None):
    '''install a set of files'''
    for f in TO_LIST(files):
        install_file(bld, destdir, f, chmod=chmod, flat=flat,
                     python_fixup=python_fixup, perl_fixup=perl_fixup,
                     destname=destname, base_name=base_name)
Build.BuildContext.INSTALL_FILES = INSTALL_FILES


def INSTALL_WILDCARD(bld, destdir, pattern, chmod=MODE_644, flat=False,
                     python_fixup=False, exclude=None, trim_path=None):
    '''install a set of files matching a wildcard pattern'''
    files=TO_LIST(bld.path.ant_glob(pattern, flat=True))
    if trim_path:
        files2 = []
        for f in files:
            files2.append(os.path.relpath(f, trim_path))
        files = files2

    if exclude:
        for f in files[:]:
            if fnmatch.fnmatch(f, exclude):
                files.remove(f)
    INSTALL_FILES(bld, destdir, files, chmod=chmod, flat=flat,
                  python_fixup=python_fixup, base_name=trim_path)
Build.BuildContext.INSTALL_WILDCARD = INSTALL_WILDCARD

def INSTALL_DIR(bld, path, chmod=0o755, env=None):
    """Install a directory if it doesn't exist, always set permissions."""

    if not path:
        return []

    destpath = bld.EXPAND_VARIABLES(path)
    if Options.options.destdir:
        destpath = os.path.join(Options.options.destdir, destpath.lstrip(os.sep))

    if bld.is_install > 0:
        if not os.path.isdir(destpath):
            try:
                Logs.info('* create %s', destpath)
                os.makedirs(destpath)
                os.chmod(destpath, chmod)
            except OSError as e:
                if not os.path.isdir(destpath):
                    raise Errors.WafError("Cannot create the folder '%s' (error: %s)" % (path, e))
Build.BuildContext.INSTALL_DIR = INSTALL_DIR

def INSTALL_DIRS(bld, destdir, dirs, chmod=0o755, env=None):
    '''install a set of directories'''
    destdir = bld.EXPAND_VARIABLES(destdir)
    dirs = bld.EXPAND_VARIABLES(dirs)
    for d in TO_LIST(dirs):
        INSTALL_DIR(bld, os.path.join(destdir, d), chmod, env)
Build.BuildContext.INSTALL_DIRS = INSTALL_DIRS


def MANPAGES(bld, manpages, install):
    '''build and install manual pages'''
    bld.env.MAN_XSL = 'http://docbook.sourceforge.net/release/xsl/current/manpages/docbook.xsl'
    for m in manpages.split():
        source = m + '.xml'
        bld.SAMBA_GENERATOR(m,
                            source=source,
                            target=m,
                            group='final',
                            rule='${XSLTPROC} --xinclude -o ${TGT} --nonet ${MAN_XSL} ${SRC}'
                            )
        if install:
            bld.INSTALL_FILES('${MANDIR}/man%s' % m[-1], m, flat=True)
Build.BuildContext.MANPAGES = MANPAGES

def SAMBAMANPAGES(bld, manpages, extra_source=None):
    '''build and install manual pages'''
    bld.env.SAMBA_EXPAND_XSL = bld.srcnode.abspath() + '/docs-xml/xslt/expand-sambadoc.xsl'
    bld.env.SAMBA_MAN_XSL = bld.srcnode.abspath() + '/docs-xml/xslt/man.xsl'
    bld.env.SAMBA_CATALOG = bld.bldnode.abspath() + '/docs-xml/build/catalog.xml'
    bld.env.SAMBA_CATALOGS = 'file:///etc/xml/catalog file:///usr/local/share/xml/catalog file://' + bld.env.SAMBA_CATALOG

    for m in manpages.split():
        source = [m + '.xml']
        if extra_source is not None:
            source = [source, extra_source]
        # ${SRC[1]} and ${SRC[2]} are not referenced in the
        # SAMBA_GENERATOR but trigger the dependency calculation so
        # ensures that manpages are rebuilt when these change.
        source += ['build/DTD/samba.entities', 'build/DTD/samba.build.version']
        bld.SAMBA_GENERATOR(m,
                            source=source,
                            target=m,
                            group='final',
                            dep_vars=['SAMBA_MAN_XSL', 'SAMBA_EXPAND_XSL', 'SAMBA_CATALOG'],
                            rule='''XML_CATALOG_FILES="${SAMBA_CATALOGS}"
                                    export XML_CATALOG_FILES
                                    ${XSLTPROC} --xinclude --stringparam noreference 0 -o ${TGT}.xml --nonet ${SAMBA_EXPAND_XSL} ${SRC[0].abspath(env)}
                                    ${XSLTPROC} --nonet -o ${TGT} ${SAMBA_MAN_XSL} ${TGT}.xml'''
                            )
        bld.INSTALL_FILES('${MANDIR}/man%s' % m[-1], m, flat=True)
Build.BuildContext.SAMBAMANPAGES = SAMBAMANPAGES

@after('apply_link')
@feature('cshlib')
def apply_bundle_remove_dynamiclib_patch(self):
    if self.env['MACBUNDLE'] or getattr(self,'mac_bundle',False):
        if not getattr(self,'vnum',None):
            try:
                self.env['LINKFLAGS'].remove('-dynamiclib')
                self.env['LINKFLAGS'].remove('-single_module')
            except ValueError:
                pass
