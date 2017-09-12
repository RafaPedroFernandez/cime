"""
short term archiving
"""

import shutil, glob, re, os

from CIME.XML.standard_module_setup import *
from CIME.case_submit               import submit
from CIME.utils                     import run_and_log_case_status, ls_sorted_by_mtime, symlink_force
from os.path                        import isdir, join
import datetime

logger = logging.getLogger(__name__)

###############################################################################
def _get_archive_file_fn(copy_only):
###############################################################################
    """
    Returns the function to use for archiving some files
    """
    return shutil.copyfile if copy_only else shutil.move


###############################################################################
def _get_datenames(case, last_date=None):
###############################################################################
    """
    Returns a list of datenames giving the dates of cpl restart files

    If there are no cpl restart files, this will return []
    """

    if last_date is not None:
        try:
            last = datetime.datetime.strptime(last_date, '%Y-%m-%d')
        except ValueError:
            expect(False, 'Could not parse the last date to archive')
    logger.debug('In get_datename...')
    rundir = case.get_value('RUNDIR')
    expect(isdir(rundir), 'Cannot open directory {} '.format(rundir))
    casename = case.get_value("CASE")
    files = sorted(glob.glob(os.path.join(rundir, casename + '.cpl*.r*.nc')))
    if not files:
        expect(False, 'Cannot find a {}.cpl*.r.*.nc file in directory {} '.format(casename, rundir))
    datenames = []
    for filename in files:
        names = filename.split('.')
        datename = names[-2]
        year, month, day, _ = [int(x) for x in datename.split('-')]
        if last_date is None or (year <= last.year and month <= last.month
                                 and day <= last.day):
            datenames.append(datename)
            logger.debug('cpl dateName: {}'.format(datename))
        else:
            logger.debug('Ignoring {}'.format(datename))
    return datenames


###############################################################################
def _get_ninst_info(case, compclass):
###############################################################################

    ninst = case.get_value('NINST_' + compclass.upper())
    ninst_strings = []
    if ninst is None:
        ninst = 1
    for i in range(1,ninst+1):
        if ninst > 1:
            ninst_strings.append('_' + '{:04d}'.format(i))
        else:
            ninst_strings.append('')

    logger.debug("ninst and ninst_strings are: {} and {} for {}".format(ninst, ninst_strings, compclass))
    return ninst, ninst_strings

###############################################################################
def _get_component_archive_entries(case, archive):
###############################################################################
    """
    Each time this is generator function is called, it yields a tuple
    (archive_entry, compname, compclass) for one component in this
    case's compset components.
    """
    compset_comps = case.get_compset_components()
    compset_comps.append('cpl')
    compset_comps.append('dart')

    for compname in compset_comps:
        archive_entry = archive.get_entry(compname)
        if archive_entry is not None:
            yield(archive_entry, compname, archive_entry.get("compclass"))

###############################################################################
def _archive_rpointer_files(case, archive, archive_entry, archive_restdir,
                            datename, datename_is_last):
###############################################################################

    # archive the rpointer files associated with datename
    casename = case.get_value("CASE")
    compclass = archive.get_entry_info(archive_entry)[1]
    ninst_strings = _get_ninst_info(case, compclass)[1]

    if datename_is_last:
        # Copy of all rpointer files for latest restart date
        rundir = case.get_value("RUNDIR")
        rpointers = glob.glob(os.path.join(rundir, 'rpointer.*'))
        for rpointer in rpointers:
            shutil.copy(rpointer, os.path.join(archive_restdir, os.path.basename(rpointer)))
    else:
        # Generate rpointer file(s) for interim restarts for the one datename and each
        # possible value of ninst_strings
        if case.get_value('DOUT_S_SAVE_INTERIM_RESTART_FILES'):

            # parse env_archive.xml to determine the rpointer files
            # and contents for the given archive_entry tag
            rpointer_items = archive.get_rpointer_contents(archive_entry)

            # loop through the possible rpointer files and contents
            for rpointer_file, rpointer_content in rpointer_items:
                temp_rpointer_file = rpointer_file
                temp_rpointer_content = rpointer_content
                # put in a temporary setting for ninst_strings if they are empty
                # in order to have just one loop over ninst_strings below
                if rpointer_content is not 'unset':
                    if not ninst_strings:
                        ninst_strings = ["empty"]
                for ninst_string in ninst_strings:
                    rpointer_file = temp_rpointer_file
                    rpointer_content = temp_rpointer_content
                    if ninst_string == 'empty':
                        ninst_string = ""
                    for key, value in [('$CASE', casename),
                                       ('$DATENAME', datename),
                                       ('$NINST_STRING', ninst_string)]:
                        rpointer_file = rpointer_file.replace(key, value)
                        rpointer_content = rpointer_content.replace(key, value)

                    # write out the respect files with the correct contents
                    rpointer_file = os.path.join(archive_restdir, rpointer_file)
                    logger.info("writing rpointer_file {}".format(rpointer_file))
                    f = open(rpointer_file, 'w')
                    for output in rpointer_content.split(','):
                        f.write("{} \n".format(output))
                    f.close()


###############################################################################
def _archive_log_files(case, archive_incomplete, archive_file_fn):
###############################################################################

    dout_s_root = case.get_value("DOUT_S_ROOT")
    rundir = case.get_value("RUNDIR")
    archive_logdir = os.path.join(dout_s_root, 'logs')
    if not os.path.exists(archive_logdir):
        os.makedirs(archive_logdir)
        logger.debug("created directory {} ".format(archive_logdir))

    if archive_incomplete == False:
        log_search = '*.log.*.gz'
    else:
        log_search = '*.log.*'

    logfiles = glob.glob(os.path.join(rundir, log_search))
    for logfile in logfiles:
        srcfile = join(rundir, os.path.basename(logfile))
        destfile = join(archive_logdir, os.path.basename(logfile))
        archive_file_fn(srcfile, destfile)
        logger.info("moving \n{} to \n{}".format(srcfile, destfile))

###############################################################################
def _archive_history_files(case, archive, archive_entry,
                           compclass, compname, histfiles_savein_rundir,
                           archive_file_fn):
###############################################################################
    """
    perform short term archiving on history files in rundir
    """

    # determine history archive directory (create if it does not exist)
    dout_s_root = case.get_value("DOUT_S_ROOT")
    casename = case.get_value("CASE")
    archive_histdir = os.path.join(dout_s_root, compclass, 'hist')
    if not os.path.exists(archive_histdir):
        os.makedirs(archive_histdir)
        logger.debug("created directory {}".format(archive_histdir))

    # determine ninst and ninst_string
    ninst, ninst_string = _get_ninst_info(case, compclass)

    # archive history files - the only history files that kept in the
    # run directory are those that are needed for restarts
    rundir = case.get_value("RUNDIR")
    for suffix in archive.get_hist_file_extensions(archive_entry):
        for i in range(ninst):
            if compname == 'dart':
                newsuffix = casename + suffix
            elif compname.find('mpas') == 0:
                newsuffix = compname + '.*' + suffix
            else:
                if ninst_string:
                    newsuffix = casename + '.' + compname + ".*" + ninst_string[i] + suffix
                else:
                    newsuffix = casename + '.' + compname + ".*" + suffix
            logger.debug("short term archiving suffix is {} ".format(newsuffix))
            pfile = re.compile(newsuffix)
            histfiles = [f for f in os.listdir(rundir) if pfile.search(f)]
            if histfiles:
                logger.debug("hist files are {} ".format(histfiles))
                for histfile in histfiles:
                    srcfile = join(rundir, histfile)
                    expect(os.path.isfile(srcfile),
                           "history file {} does not exist ".format(srcfile))
                    destfile = join(archive_histdir, histfile)
                    if histfile in histfiles_savein_rundir:
                        logger.info("copying \n{} to \n{} ".format(srcfile, destfile))
                        shutil.copy(srcfile, destfile)
                    else:
                        logger.info("moving \n{} to \n{} ".format(srcfile, destfile))
                        archive_file_fn(srcfile, destfile)

###############################################################################
def get_histfiles_for_restarts(case, archive, archive_entry, restfile):
###############################################################################

    # determine history files that are needed for restarts
    histfiles = []
    rest_hist_varname = archive.get_entry_value('rest_history_varname', archive_entry)
    if rest_hist_varname != 'unset':
        rundir = case.get_value("RUNDIR")
        cmd = "ncdump -v {} {} ".format(rest_hist_varname, os.path.join(rundir, restfile))
        rc, out, error = run_cmd(cmd)
        if rc != 0:
            logger.debug(" WARNING: {} failed rc={:d}\nout={}\nerr={}".format(cmd, rc, out, error))

        searchname = "{} =".format(rest_hist_varname)
        if searchname in out:
            offset = out.index(searchname)
            items = out[offset:].split(",")
            for item in items:
                # the following match has an option of having a './' at the beginning of
                # the history filename
                matchobj = re.search(r"\"(\.*\/*\w.*)\s?\"", item)
                if matchobj:
                    histfile = matchobj.group(1).strip()
                    histfile = os.path.basename(histfile)
                    # append histfile to the list ONLY if it exists in rundir before the archiving
                    if os.path.isfile(os.path.join(rundir,histfile)):
                        histfiles.append(histfile)
    return histfiles

###############################################################################
def _archive_restarts_date(case, archive,
                           datename, datename_is_last,
                           archive_restdir, archive_file_fn,
                           link_to_last_restart_files=False):
###############################################################################
    """
    Archive restart files for a single date

    Returns a dictionary of histfiles that need saving in the run
    directory, indexed by compname
    """
    logger.info('-------------------------------------------')
    logger.info('Archiving restarts for date {}'.format(datename))
    logger.info('-------------------------------------------')

    histfiles_savein_rundir_by_compname = {}

    for (archive_entry, compname, compclass) in _get_component_archive_entries(case, archive):
        logger.info('Archiving restarts for {} ({})'.format(compname, compclass))

        # archive restarts
        histfiles_savein_rundir = _archive_restarts_date_comp(case, archive, archive_entry,
                                                              compclass, compname,
                                                              datename, datename_is_last,
                                                              archive_restdir, archive_file_fn,
                                                              link_to_last_restart_files)
        histfiles_savein_rundir_by_compname[compname] = histfiles_savein_rundir

    return histfiles_savein_rundir_by_compname

###############################################################################
def _archive_restarts_date_comp(case, archive, archive_entry,
                                compclass, compname, datename, datename_is_last,
                                archive_restdir, archive_file_fn,
                                link_to_last_restart_files=False):
###############################################################################
    """
    Archive restart files for a single date and single component

    If link_to_last_restart_files is True, then make a symlink to the
    last set of restart files (i.e., the set with datename_is_last
    True); if False (the default), copy them. (This has no effect on the
    history files that are associated with these restart files.)
    """

    rundir = case.get_value("RUNDIR")
    casename = case.get_value("CASE")
    if datename_is_last or case.get_value('DOUT_S_SAVE_INTERIM_RESTART_FILES'):
        if not os.path.exists(archive_restdir):
            os.makedirs(archive_restdir)

    # archive the rpointer file(s) for this datename and all possible ninst_strings
    _archive_rpointer_files(case, archive, archive_entry, archive_restdir,
                            datename, datename_is_last)

    # determine ninst and ninst_string
    ninst, ninst_strings = _get_ninst_info(case, compclass)

    # move all but latest restart files into the archive restart directory
    # copy latest restart files to archive restart directory
    histfiles_savein_rundir = []

    # determine function to use for last set of restart files
    if link_to_last_restart_files:
        last_restart_file_fn = symlink_force
        last_restart_file_fn_msg = "linking"
    else:
        last_restart_file_fn = shutil.copy
        last_restart_file_fn_msg = "copying"

    # get file_extension suffixes
    for suffix in archive.get_rest_file_extensions(archive_entry):
        for i in range(ninst):
            restfiles = ""
            if compname.find("mpas") == 0:
                pattern = compname + suffix + '_'.join(datename.rsplit('-', 1))
                pfile = re.compile(pattern)
                restfiles = [f for f in os.listdir(rundir) if pfile.search(f)]
            else:
                pattern = r"{}\.{}\d*.*".format(casename, compname)
                if "dart" not in pattern:
                    pfile = re.compile(pattern)
                    files = [f for f in os.listdir(rundir) if pfile.search(f)]
                    if ninst_strings:
                        pattern = ninst_strings[i] + suffix + datename
                        pfile = re.compile(pattern)
                        restfiles = [f for f in files if pfile.search(f)]
                    else:
                        pattern = suffix + datename
                        pfile = re.compile(pattern)
                        restfiles = [f for f in files if pfile.search(f)]
                else:
                    pattern = suffix
                    pfile = re.compile(pattern)
                    restfiles = [f for f in os.listdir(rundir) if pfile.search(f)]

            for restfile in restfiles:
                restfile = os.path.basename(restfile)

                # obtain array of history files for restarts
                # need to do this before archiving restart files
                histfiles_for_restart = get_histfiles_for_restarts(case, archive,
                                                                   archive_entry, restfile)

                if datename_is_last and histfiles_for_restart:
                    for histfile in histfiles_for_restart:
                        if histfile not in histfiles_savein_rundir:
                            histfiles_savein_rundir.append(histfile)

                # archive restart files and all history files that are needed for restart
                # Note that the latest file should be copied and not moved
                if datename_is_last:
                    srcfile = os.path.join(rundir, restfile)
                    destfile = os.path.join(archive_restdir, restfile)
                    last_restart_file_fn(srcfile, destfile)
                    logger.info("{} \n{} to \n{}".format(
                        last_restart_file_fn_msg, srcfile, destfile))
                    for histfile in histfiles_for_restart:
                        srcfile = os.path.join(rundir, histfile)
                        destfile = os.path.join(archive_restdir, histfile)
                        expect(os.path.isfile(srcfile),
                               "restart file {} does not exist ".format(srcfile))
                        shutil.copy(srcfile, destfile)
                        logger.info("copying \n{} to \n{}".format(srcfile, destfile))
                else:
                    # Only archive intermediate restarts if requested - otherwise remove them
                    if case.get_value('DOUT_S_SAVE_INTERIM_RESTART_FILES'):
                        srcfile = os.path.join(rundir, restfile)
                        destfile = os.path.join(archive_restdir, restfile)
                        logger.info("moving \n{} to \n{}".format(srcfile, destfile))
                        expect(os.path.isfile(srcfile),
                               "restart file {} does not exist ".format(srcfile))
                        archive_file_fn(srcfile, destfile)
                        logger.info("moving \n{} to \n{}".format(srcfile, destfile))

                        # need to copy the history files needed for interim restarts - since
                        # have not archived all of the history files yet
                        for histfile in histfiles_for_restart:
                            srcfile = os.path.join(rundir, histfile)
                            destfile = os.path.join(archive_restdir, histfile)
                            expect(os.path.isfile(srcfile),
                                   "hist file {} does not exist ".format(srcfile))
                            shutil.copy(srcfile, destfile)
                            logger.info("copying \n{} to \n{}".format(srcfile, destfile))
                    else:
                        srcfile = os.path.join(rundir, restfile)
                        logger.info("removing interim restart file {}".format(srcfile))
                        if (os.path.isfile(srcfile)):
                            try:
                                os.remove(srcfile)
                            except OSError:
                                logger.warn("unable to remove interim restart file {}".format(srcfile))
                        else:
                            logger.warn("interim restart file {} does not exist".format(srcfile))

    return histfiles_savein_rundir

###############################################################################
def _archive_process(case, archive, last_date, archive_incomplete_logs, copy_only):
###############################################################################
    """
    Parse config_archive.xml and perform short term archiving
    """

    logger.debug('In archive_process...')

    archive_file_fn = _get_archive_file_fn(copy_only)

    # archive log files
    _archive_log_files(case, archive_incomplete_logs, archive_file_fn)

    # archive restarts and all necessary associated files (e.g. rpointer files)
    histfiles_savein_rundir_by_compname = {}
    dout_s_root = case.get_value("DOUT_S_ROOT")
    datenames = _get_datenames(case, last_date)
    for datename in datenames:
        datename_is_last = False
        if datename == datenames[-1]:
            datename_is_last = True

        archive_restdir = join(dout_s_root, 'rest', datename)
        histfiles_savein_rundir_by_compname_this_date = _archive_restarts_date(
            case, archive, datename, datename_is_last, archive_restdir, archive_file_fn)
        if datename_is_last:
            histfiles_savein_rundir_by_compname = histfiles_savein_rundir_by_compname_this_date

    # archive history files
    for (archive_entry, compname, compclass) in _get_component_archive_entries(case, archive):
        logger.info('Archiving history files for {} ({})'.format(compname, compclass))
        histfiles_savein_rundir = histfiles_savein_rundir_by_compname.get(compname, [])
        logger.info("histfiles_savein_rundir {} ".format(histfiles_savein_rundir))
        _archive_history_files(case, archive, archive_entry,
                               compclass, compname, histfiles_savein_rundir,
                               archive_file_fn)

###############################################################################
def restore_from_archive(case, rest_dir=None):
###############################################################################
    """
    Take archived restart files and load them into current case.  Use rest_dir if provided otherwise use most recent
    """
    dout_sr = case.get_value("DOUT_S_ROOT")
    rundir = case.get_value("RUNDIR")
    if rest_dir is not None:
        if not os.path.isabs(rest_dir):
            rest_dir = os.path.join(dout_sr, "rest", rest_dir)
    else:
        rest_dir = ls_sorted_by_mtime(os.path.join(dout_sr, "rest"))[-1]

    logger.info("Restoring from {} to {}".format(rest_dir, rundir))
    for item in glob.glob("{}/*".format(rest_dir)):
        base = os.path.basename(item)
        dst = os.path.join(rundir, base)
        if os.path.exists(dst):
            os.remove(dst)

        shutil.copy(item, rundir)

###############################################################################
def archive_last_restarts(case, archive_restdir, link_to_restart_files=False):
###############################################################################
    """
    Convenience function for archiving just the last set of restart
    files to a given directory. This also saves files attached to the
    restart set, such as rpointer files and necessary history
    files. However, it does not save other files that are typically
    archived (e.g., history files, log files).

    Files are copied to the directory given by archive_restdir.

    If link_to_restart_files is True, then symlinks rather than copies
    are done for the restart files. (This has no effect on the history
    files that are associated with these restart files.)
    """
    archive = case.get_env('archive')
    datenames = _get_datenames(case)
    expect(len(datenames) >= 1, "No restart dates found")
    last_datename = datenames[-1]

    # Not currently used for anything if we're only archiving the last
    # set of restart files, but needed to satisfy the following interface
    archive_file_fn = _get_archive_file_fn(copy_only=False)

    _ = _archive_restarts_date(case=case,
                               archive=archive,
                               datename=last_datename,
                               datename_is_last=True,
                               archive_restdir=archive_restdir,
                               archive_file_fn=archive_file_fn,
                               link_to_last_restart_files=link_to_restart_files)

###############################################################################
def case_st_archive(case, last_date=None, archive_incomplete_logs=True, copy_only=False, no_resubmit=False):
###############################################################################
    """
    Create archive object and perform short term archiving
    """
    caseroot = case.get_value("CASEROOT")

    dout_s_root = case.get_value('DOUT_S_ROOT')
    if dout_s_root is None or dout_s_root == 'UNSET':
        expect(False,
               'XML variable DOUT_S_ROOT is required for short-term achiver')
    if not isdir(dout_s_root):
        os.makedirs(dout_s_root)

    dout_s_save_interim = case.get_value('DOUT_S_SAVE_INTERIM_RESTART_FILES')
    if dout_s_save_interim == 'FALSE' or dout_s_save_interim == 'UNSET':
        rest_n = case.get_value('REST_N')
        stop_n = case.get_value('STOP_N')
        if rest_n < stop_n:
            logger.warn('Restart files from end of run will be saved'
                        'interim restart files will be deleted')

    logger.info("st_archive starting")

    archive = case.get_env('archive')
    functor = lambda: _archive_process(case, archive, last_date, archive_incomplete_logs, copy_only)
    run_and_log_case_status(functor, "st_archive", caseroot=caseroot)

    logger.info("st_archive completed")

    # resubmit case if appropriate
    resubmit = case.get_value("RESUBMIT")
    if resubmit > 0 and not no_resubmit:
        logger.info("resubmitting from st_archive, resubmit={:d}".format(resubmit))
        if case.get_value("MACH") == "mira":
            expect(os.path.isfile(".original_host"), "ERROR alcf host file not found")
            with open(".original_host", "r") as fd:
                sshhost = fd.read()
            run_cmd("ssh cooleylogin1 ssh {} '{}/case.submit {} --resubmit' "\
                        .format(sshhost, caseroot, caseroot), verbose=True)
        else:
            submit(case, resubmit=True)

    return True