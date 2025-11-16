#!/bin/sh

# VALD_HOME is one level above this script
if [ ! "${VALD_HOME}" ]; then
  cd `dirname $0`/..
  VALD_HOME=`pwd`
  cd -
fi
export VALD_HOME

#if [ "${VALD_HOME}" ]; then
#  echo Using non-default VALD_HOME directory: ${VALD_HOME}
#else
#  VALD_HOME=${HOME}
#fi

# Include site-specific configuration variables
VALD_CONFIG=${VALD_HOME}/CONFIG
source ${VALD_CONFIG}/local_mirror_config.sh

# Directory and filename definitions
VALD_LOG_DIR="${VALD_HOME}/LOGS"
EMS_SCRIPT="${VALD_HOME}/EMS/service-atd.sh"
EMS_WORKING_DIR="${VALD_HOME}/EMS/TMP_WORKING"
EMS_SERVICE_LOG="${VALD_LOG_DIR}/service.log"
VALD_FTP_DIR="${VALD_HOME}/WWW/public_html/FTP"

# Make sure the right path is set
PATH="${PATH}:${VALD_HOME}/bin"

# Go to the working directory
cd ${EMS_WORKING_DIR}

# Dump the environment (for debugging purposes)
# env

# Prepare
echo "=====Starting $EMS_SCRIPT =========`date`====" >> $EMS_SERVICE_LOG
rm -f request.* job.* result.* select.input TMP.LIST TMP1.LIST process >& /dev/null
rm -f `find $VALD_FTP_DIR -maxdepth 1 -ctime +2 -type f`
echo "-----Removed temporary files and cleaned up FTP dir" >> $EMS_SERVICE_LOG

# Parse the mailfile, empty it, and make some log entries
${VALD_HOME}/bin/parsemail
touch empty
cp empty $MAIL_SPOOL
rm empty
echo "-----Created new (empty) $MAIL_SPOOL" >> $EMS_SERVICE_LOG
chmod u+x process
cp -f process $VALD_LOG_DIR/last.process
echo "-----Last process:" >> $EMS_SERVICE_LOG
NUMPROC=`wc -l process | awk '{print $1}'`
cat process >> $EMS_SERVICE_LOG

# Now execute the prepared process
./process > $VALD_LOG_DIR/last_process_output 2>&1
if [ $? != 0 ]; then
  echo "ERROR while processing" >> $VALD_LOG_DIR/last_process_output
  date >> $VALD_LOG_DIR/last_process_output
  cat $VALD_LOG_DIR/last_process_output >> $VALD_LOG_DIR/ems_error.log
fi
# Get the output to stdout anyway...

# Reschedule the EMS script
at -f $EMS_SCRIPT now + 10 minutes || (echo "Scheduling of the at-job failed - do something about this!" >> $VALD_LOG_DIR/last_process_output)

cat $VALD_LOG_DIR/last_process_output

echo "-----Done!" >> $EMS_SERVICE_LOG
