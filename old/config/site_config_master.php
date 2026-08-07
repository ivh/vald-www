<?php

# Master configuration file for VALD Web interface

$config['version']		= "0.5.0";
$config['versdate']		= "2011-12-16";	

$config['clients_register']	= "{$config['VALD_root']}/EMS/clients.register";
$config['persconfig_dir']	= "{$config['VALD_root']}/EMS/PERSONAL_CONFIG";
$config['persconfig_default']	= "{$config['VALD_root']}/CONFIG/default.cfg";
$config['htmlconfig_default']	= "{$config['VALD_root']}/CONFIG/htmldefault.cfg";
$config['html_template_dir']	= "{$config['VALD_root']}/WWW/interface";
$config['request_template_dir']	= "{$config['VALD_root']}/WWW/requests";
$config['documentation_dir']	= "{$config['VALD_root']}/WWW/documentation";
$config['news_dir']		= "{$config['VALD_root']}/WWW/news";
$config['style_file']		= "{$config['VALD_root']}/WWW/style/style.css";

?>
