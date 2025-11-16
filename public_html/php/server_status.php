<?php

# Read configuration files...
#
# First the local config:
include('../../config/site_config_local.php');

# Then the master config:
include('../../config/site_config_master.php');

# List of networks that are allowed to view this page
$networks_allowed = array("/::1/", "/^127\.0\.0.*/"); # , "/^130\.238\.192\..*/");
# $networks_allowed = array("/::1/", "/^127\.0\.0.*/", "/^130\.238\.192\..*/", "/^83\.149\.230\.175/", "/^93\.180\.26\.144/");

# Check against list of networks
$allowed = False;
foreach ($networks_allowed as $network) {
	if (preg_match($network, $_SERVER['REMOTE_ADDR'])) {
		$allowed = True;
	}
}

# Bail out if not allowed
if (!$allowed)	{
	header("location: ${config['thisscript']}");
	exit();
}

# If here, start creating a server info page

# Extract the local svn version number
$svnversion=null;
$retval=null;
exec("/usr/bin/svnversion ${config['VALD_root']} 2>&1", $svnversion,$retval);

$version[] = "<h3>Server: ${config['sitename']}</h3>\n";
$version[] = "<p>Version: $svnversion[0]\n";

exec("svnversion ${config['VALD_root']} 2>&1", $svnversion);
exec("svn info ${config['VALD_root']} 2>&1", $svn_info);
exec("svn status ${config['VALD_root']} 2>&1", $svn_status);

print "<pre>\n";

print "Server: ${config['sitename']}\n$svnversion[0]\n\n";

#print "> svn info ${config['VALD_root']}\n";
foreach ($svn_info as $line) {
	print "$line\n";
}

#print "> svn status ${config['VALD_root']}\n";
foreach ($svn_status as $line) {
	print "$line\n";
}

print "</pre>\n";

print "<a href=\"${config['thisscript']}\">Back to the VALD server\n";

?>
