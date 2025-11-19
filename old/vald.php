<?php

####################################################################################################################
# 1 - HELPER FUNCTIONS
####################################################################################################################

function EditLine ($line, $replace) {

	# This function replaces any occurences of the keys in the $replace
	# array with their values. Useful for generating dynamic content.

	if (isset($replace)) {
		foreach ($replace as $key => $value) {
			if ($value) {
				$line = preg_replace("/\\$$key/", "$value", $line);
			} else if ($value == "0") {
				$line = preg_replace("/\\$$key/", "$value", $line);
			} else {
				# If no value is given, remove the key from the string
				# Note special removal of additional ',' used in request templates
				$line = preg_replace("/\\$$key,?/", "", $line);
			}
		}

		# Remove any remaining unmatched $-strings - note the single quotes to correctly interpret '$'
		$line = preg_replace('/\$\w+/', "", $line);
	}

	return $line;

}

####################################################################################################################

function Output ($array, $replace=array()) {

	# Function to print an array, after modifying it with EditLine()

	foreach ($array as $line) {
		print EditLine($line, $replace);
	}


}

####################################################################################################################

function SpamCheck ( $message ) {

	# Return 'false' if any of our criteria for spam are met.

	# Message for sure too short
        if ( strlen($message) < 10 ) return false;

	# Message contains url
	if ( stripos(str_replace(" ","",$message),"ahref=")  !== false ) return false;
	if ( stripos(str_replace(" ","",$message),"[url")    !== false ) return false;
	if ( stripos(str_replace(" ","",$message),"[/url")   !== false ) return false;
	if ( stripos(str_replace(" ","",$message),"http://") !== false ) return false;
	if ( stripos(str_replace(" ","",$message),"https://") !== false ) return false;

	return true;
}

####################################################################################################################

function Writefile ($filename, $output) {

	# Do the actual writing to file
	$fh = fopen("$filename", "w");
	for ($i=0; $i<count($output); $i++) {
		# The '@' suppresses error output. If the file was inaccessible,
		# there would already have been an error at fopen().
		fwrite($fh, $output[$i]);
	}
	# Same again here with the error suppression
	@fclose($fh);

	# Nex, update the file umask, such that anyone can read and write
	# This is necessary, since the script runs (normally) under the
	# UID of the web server
	#
	# The '@' before the function call suppresses error reporting,
	# because it may be the case that this will not succeed if the
	# file is still owned by 'vald', instead of by the web server
	# (like: httpd, www-data, etc). This is not a problem, as long
	# as the file had mode 0666 in the first place.
	@chmod($filename, 0666);

}


####################################################################################################################

function MakeAllHTML ($stylefile, $topleft, $topright, $navigation, $content, $replace) {

	# Master function that sets up the entire output page (including page start and end) :
	#
	#	+-----------------------------------------------+
	#	|	$topleft	|	$topright	|
	#	+-----------------------+-----------------------|
	#	|	$navigation	|	$content	|
	#	+-----------------------+-----------------------|
	

	# The following PHP command (header) actually requires a value - commented out... - could be removed
	# header();

	# Use the 'Output' function to modify the content on-the-fly

	print "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01//EN\" \"http://www.w3.org/TR/html4/strict.dtd\">\n";
	print "<html><head><title>VALD WWW interface</title><style type=\"text/css\">\n";

	Output($stylefile, $replace);

	print "</style></head>\n";
	print "<table border=\"0\" cellspacing=\"0\" cellpadding=\"5px\" width=\"100%\">\n";
	print "<td class=\"version\" valign=\"top\">\n";

	Output($topleft, $replace);

	print "<td class=\"topframe\">\n"; 

	Output($topright, $replace);

	print "<tr>";
	print "<td class=\"navigate\">\n";

	Output($navigation, $replace);

	print "<td class=\"content\">\n"; 

	Output($content, $replace);

	print "</table></body></html>";

}

####################################################################################################################

function file_contents($dirname, $filename) {

  # Function that performs directory checking in order to avoid 'Directory Traversal' attacks.

  $dirnamelength = strlen($dirname);
  $filepath = realpath($dirname . $filename);
  
  # Now check that basedir did not change
  if (substr($filepath, 0, $dirnamelength) == $dirname) {
    $outputdata = file($dirname . $filename);
  } else {
    $outputdata[] = "Error in filename resolution\n";
  }
  
  return $outputdata;

}

####################################################################################################################

function safe_mail($send_request_to, $subject, $mail_content, $send_request_from) {

	# Do some validation and cleaning before sending an email in order to avoid email header injection

	if (! preg_match('/\@localhost$/', $send_request_to)) {
		$send_request_to = filter_var($send_request_to, FILTER_VALIDATE_EMAIL);
	}

	$subject = str_replace(array("\r","\n"),array(" "," "),$subject);

	if ($send_request_from) { $send_request_from = filter_var($send_request_from, FILTER_VALIDATE_EMAIL); }

	if (!($send_request_to && $mail_content)) {
		print "$send_request_to $send_request_from";
		return false;
	}

	mail($send_request_to, $subject, $mail_content, "From: {$send_request_from}");
	return true;
	
}

####################################################################################################################
# 2 - DEFINED CLASSES
####################################################################################################################

class User {

	# Class to store user properties. Currently only the
	# email, and a boolean indicating if the user is
	# registered.
	#
	# This structure can later also be used for user preferences?

	var $localuser = False;
	var $registered = False;
	var $persconfig_file = "";
	var $htmlconfig_file = "";
	var $html_defaults = array();

	function __construct ($email="") {

		$this->email = strtolower($email);

	}

	
	function Validate ($clients_register) {
	
		# This function checks the user's email address
		# agains the client register.
			
		$content = file($clients_register);
				
		foreach ($content as $line) {

			# Use regexp to extract full user names
			preg_match('/^#\$\s+(.*)/', $line, $matches);
			if ($matches) { $currentname = trim($matches[1]); };

			# Non-commented lines are email addresses
			$line = strtolower(trim(preg_replace("/\n/", "", $line)));
			if ($this->email == $line) {
				$this->name = $currentname;

				# Set the 'register' flag
				$this->registered = True;
			}
		}
				
	}
	
	function ReadHtmlDefs ($filename) {
		
		if (!isset($filename)) { $filename=$this->htmlconfig_file; }

		# Return (quietly) if file does not exist
		if (!file_exists($filename)) { 
			print("Cannot open HTML settings file on disk: $filename\n");
			return;
		}

		$content = file($filename);

		# Now extract, line by line, the linelists and parameters
		foreach ($content as $line) {
		
			$line = preg_replace("/\n/", "", $line);
			$fields = preg_split('/\s+/', $line, 2);
			if (count($fields) != 2) { continue; }		
		
			# Cleanup whitespace
			for ($j=0; $j<count($fields); $j++) {
				$fields[$j] = trim($fields[$j]);
			}
			$this->html_defaults[$fields[0]] = $fields[1];
			# print "$fields[0] - $fields[1] <br>\n";
		
		}
		
		
	
	}

	function SaveHtmlDefs ($filename) {
	
		# Update the filename if given explicitly - otherwise use default
		if (!isset($filename)) { $filename=$this->htmlconfig_file; }
		
		$output = array();
		
		foreach ($this->html_defaults as $key => $value) {
			$output[] = "$key\t$value\n";
		}
		
		WriteFile($filename, $output);

	}


}

####################################################################################################################

class LineList {

	# This class defines a single linelist, with identifiers for its
	# name, parameters and 'commented' boolean (indicating if the list
	# is active). Also some flags indicating differences with the default
	# linelist

	var $name        = "";
	var $id          = 0;
	var $params      = array();
	var $commented   = False;
	var $mod_comment = False;
	var $mod_params  = array();

	function __construct ($name, $id, $params, $commented) {

		# Generating function

		$this->name      = $name;
		$this->id        = $id;
		$this->params    = $params;
		$this->commented = $commented;

		for ($i=1; $i<count($params); $i++) {
			$this->mod_params[$i] = False;
		}

	}

}
####################################################################################################################

class PersConfig {

	# Quite an extended class that defines the complete personal configuration
	# file for a single user, including all linelists. Functions for manipulating
	# the linelists and reading/writing/comparing are provided

	var $filename    = "";
	var $hiddenparam = array();
	var $linelists   = array();
	var $n_linelists = 0;


	function __construct  ($filename) {

		# Generating function
		if ($filename) { $this->ReadFile($filename); }

	}


	function AddLineList ($commented, $parameters) {

		# Add one linelist to the set of linelists
		$nlists = $this->n_linelists;
		$dummy = preg_match("/'(.*)'/", $parameters[14], $matches);
		$this->linelists[$nlists] = new LineList($matches[1], $parameters[1], $parameters, $commented);
		$this->n_linelists++;

	}


	function DeleteLineList ($index) {

		# Remove a linelist from the set of linelists	
		unset($this->linelists[$index]);

		# Reindex the array (don't forget this, or the indices will be screwed up)
		$this->linelists = array_values($this->linelists);
 		$this->n_linelists--;

	}


	function ReadFile ($filename) {

		# Read a personal configuration file from disk
			
		$this->filename = $filename;
		# print "Reading $this->filename<br>\n";

		# Return (quietly) if file does not exist
		if (!file_exists($filename)) { 
			# trigger_error("Cannot open personal configuration file on disk");
			return;
		}
		
		$content = file($filename);

		# Extract the first four parameters from the first line
		# Currently called 'hidden' parameters, since the
		# user cannot modify these (yet)
		$content[0] = preg_replace("/\n/", "", $content[0]);
		$this->hiddenparam = preg_split('/,/', $content[0]);
		for ($j=0; $j<count($this->hiddenparam)-1; $j++) {
			$this->hiddenparam[$j] = trim($this->hiddenparam[$j]);
		}
		
		# Now extract, line by line, the linelists and parameters
		for ($i=1; $i<count($content); $i++) {

			$content[$i] = preg_replace("/\n/", "", $content[$i]);
			$fields = preg_split('/,/', $content[$i], 15);
			if (count($fields) != 15) { continue; }

			# Trim whitespace and quotes from all fields except the last one
			for ($j=0; $j<14; $j++) {
				$fields[$j] = trim($fields[$j]);
				$fields[$j] = preg_replace("/'/", "", $fields[$j]);
			}

			# A ';' sign indicates that the list is currently not active	
			
			if (preg_match("/^;[^']/", $content[$i])) { continue; }
			
			$commented = preg_match("/^;/", $content[$i]);
			$fields[0] = preg_replace("/;/", "", $fields[0]);
			
			# Finally, add the list to this structure
			$this->AddLineList($commented, $fields);

		}
	
	}


	function WriteFile ($filename="") {

		# Write a personal configuration file to disk

		# Update the filename if given explicitly - otherwise use default
		if ($filename != "") { $this->filename = $filename; }
		
		$output = array();

		# Store the 'hidden' parameters
		for ($i=0; $i<4; $i++) {
			$output[] = "{$this->hiddenparam[$i]},";
		}

		$output[] = "\n";

		# Now write, line by line, each linelist and parameters
		for ($i=0; $i<$this->n_linelists; $i++) {

			# OLD LOGIC: Skip linelists that are not yet integrated in the user's
			# own selection
			# if (isset($this->linelists[$i]->newly_added)) {
			# 	continue;
			# }
			
			if ($this->linelists[$i]->commented) {
				$output[] = ";";
			}
			
			$output[] = "'{$this->linelists[$i]->params[0]}', ";

			for ($j=1; $j<14; $j++) {
			       $output[] = "{$this->linelists[$i]->params[$j]}, ";
			}

			# Treat last parameter seperately (no comma, but newline)
			$output[] = "{$this->linelists[$i]->params[14]}\n";

		}
		
		# Now do the actual writing to file
		WriteFile($this->filename, $output);
	
	}

	
	function FindLineList ($id) {

		# Extract the correct index number from the user's linelists
		for ($i=0; $i<$this->n_linelists; $i++) {
			if ($this->linelists[$i]->id == $id) {
				#print "Found: {$id}<br>\n";
				return $i;
			}
		}
		
		# If no linelist was found, return -1
		return -1;
		
	}


	function SortLineLists () {

		# Sort the set of linelists by identifier	
		$id_list = array();
		
		# Create an index array
		for ($i=0; $i<$this->n_linelists; $i++) {
			$id_list[$i] = $this->linelists[$i]->id;
		}
		
		# Sort the index array
		sort($id_list, $sort_flags=SORT_NUMERIC);
		$new_linelists = array();
		
		# Reshuffle the set of linelists
		for ($i=0; $i<$this->n_linelists; $i++) {
		       $index = $this->FindLineList($id_list[$i]);
		       #print "$i/{$id_list[$i]}/$index<br>\n";
		       $new_linelists[$i] = $this->linelists[$index];
		}
		
		# Assign the sorted set of linelist
		$this->linelists = $new_linelists;
		
	}

	
	function MakeHTML ($query) {

		# Create HTML output for the individual linelists
		# Not the nicest of functions, since it includes
		# HTML output, but it should go somewhere...
	
		# Start with empty array
		$output = array();

		# Am I supposed to make a linelist editable - if yes, which?		
		$edit = 0;  // Default value
		if (isset($query['action']) && $query['action'] === 'edit') {
		    $edit = $query['editid'] ?? 0;
		}

		# Possible debugging output:
		#$output[]="<tr><td colspan=10>Filename: $this->filename</td></tr>\n";

		# Here one could make some of the 'hidden' parameters editable
		#
		#for ($i=0; $i<4; $i++) {
		#	$output[] = "<input type=\"hidden\" name=\"fval$i\" value=\"{$this->hiddenparam[$i]}\">\n";
		#}

		# Step through all linelists
		for ($i=0; $i<$this->n_linelists; $i++) {

			# If this is the linelist that should be editable, modify the HTML output appropriately
			if ($edit == $this->linelists[$i]->params[1]) {

				$output[] = "<tr align=center";
				## Is this linelist new?
				#if (isset($this->linelists[$i]->newly_added)) {
				#	$output[] = " class=\"new_data\"";
				#}
				$output[] = "><td align=\"right\">{$this->linelists[$i]->id}<td";
				# Has this been parameter modified w.r.t. the default list?
				if ($this->linelists[$i]->mod_comment) {
					$output[] = " class=\"modified_data\"";
				}
				$output[] = "><input type=checkbox ";
				if (!$this->linelists[$i]->commented) {
					$output[] = "checked ";
				}
				$output[] = "name=\"linelist-checked\">\n";

				# Output the descriptive name of the linelist
				$output[] = "<td align=\"left\">{$this->linelists[$i]->name}\n";

				# These are internal parameters, and cannot be modified by the user				
				for ($j=1; $j<5; $j++) {

					$output[] = "<input type=\"hidden\" name=\"edit-val-$j\" value=\"{$this->linelists[$i]->params[$j]}\">\n";

				}

				# Now, the editable parameters
				for ($j=5; $j<14; $j++) {

					$output[] = "<td";
					# Has this been parameter modified w.r.t. the default list?
					if ($this->linelists[$i]->mod_params[$j]) {
						$output[] = " class=\"modified_data\"";
					}
					$output[] = "><input type=\"text\" name=\"edit-val-$j\" value=\"{$this->linelists[$i]->params[$j]}\" size=2>\n";

				}

				# Create some options
				$output[] = "<td><a href=\"javascript:void();\" onClick=\"thisform.action.value='save'; thisform.editid.value='{$this->linelists[$i]->id}'; thisform.submit(); return false;\">Save</a>\n";
				$output[] = "<td><a href=\"javascript:void();\" onClick=\"thisform.action.value='cancel'; thisform.editid.value='{$this->linelists[$i]->id}';thisform.submit(); return false;\">Cancel</a>\n";
				$output[] = "<td><a href=\"javascript:void();\" onClick=\"thisform.action.value='restore'; thisform.editid.value='{$this->linelists[$i]->id}'; thisform.submit(); return false;\">Restore VALD default</a>\n";

			# 'Normal' output :
			} else {

				$output[] = "<tr align=center";
				## Is this linelist new?
				#if (isset($this->linelists[$i]->newly_added)) {
				#	$output[] = " class=\"new_data\"";
				#}
				$output[] = "><td align=\"right\">{$this->linelists[$i]->id}<td";
				# Has this been parameter modified w.r.t. the default list?
				if ($this->linelists[$i]->mod_comment) {
					$output[] = " class=\"modified_data\"";
				}
				$output[] = ">";
				if (!$this->linelists[$i]->commented) {
					$output[] = " X ";
				} else $output[] = " &nbsp; ";

				# Output the descriptive name of the linelist
				$output[] = "<td align=\"left\">{$this->linelists[$i]->name}\n";

				# Output the parameters
				for ($j=5; $j<14; $j++) {

					$output[] = "<td";
					if ($this->linelists[$i]->mod_params[$j]) {
						$output[] = " class=\"modified_data\"";
					}
					$output[] = ">{$this->linelists[$i]->params[$j]}\n";

				}
				
				if ($edit != 0) {
					# When editing another linelist, output nothing here (no options)
					$output[] = "<td colspan=3> &nbsp;<br>\n";
				} else {
					# When browsing only, list options for each linelist
					#if (isset($this->linelists[$i]->newly_added)) {
					#	# If the linelist is new, allow the user to add it
					#	$output[] = "<td><a href=\"javascript:void();\" onClick=\"thisform.action.value='restore'; thisform.editid.value='{$this->linelists[$i]->id}'; thisform.submit(); return false;\">Add</a>\n";
					#	$output[] = "<td><a href=\"javascript:void();\" onClick=\"thisform.action.value='restore_all'; thisform.submit(); return false;\">Add all</a>\n";
					#} else {
						# For existing linelist, allow the user to edit it
						$output[] = "<td><a href=\"javascript:void();\"	onClick=\"thisform.action.value='edit'; thisform.editid.value='{$this->linelists[$i]->id}'; thisform.submit(); return false;\">Edit</a>\n";
					#}
				}
			}
		}

		# Save the total number of linelists
		$output[] = "<input type=\"hidden\" name=\"nelem\" value=\"{$this->n_linelists}\">\n";

		return $output;
	}


	function Update ($query) {

		# Modify the parameters of a linelist from the submitted form data

		$params = array();
		
		# Get the corresponding linelist
		$index = $this->FindLineList($query["editid"]);
		
 		# Extract each parameter from the submitted form data
		for ($j=1; $j<14; $j++) {
			if (isset($query["edit-val-{$j}"]) and is_numeric($query["edit-val-{$j}"])) {
				$params[$j] = $query["edit-val-{$j}"];
			}
		}
		# Update the 'commented' flag
		$commented = !isset($query["linelist-checked"]);

		# Update the values in the linelist
		for ($j=1; $j<14; $j++) {
			$this->linelists[$index]->params[$j] = $params[$j];
		}
		$this->linelists[$index]->commented = $commented;

		# If this linelist was recently added, accept it now into the user's configuration
		if (isset($this->linelists[$index]->newly_added)) {
			unset($this->linelists[$index]->newly_added);
		}
	}


	function Restore ($otherconfig, $query) {
	
		# Restore a particular linelist to its value from another (default) config file
		
		$ids_to_restore = array();

		if ($query["action"] == "restore_all") {
			# Restore all list?	
			for ($j=0; $j<$this->n_linelists; $j++) {
				#if (isset($this->linelists[$j]->newly_added)) {
					$ids_to_restore[] = $this->linelists[$j]->id;
				#}
			}
		} else {
			# Or, restore only one single list?
			$ids_to_restore[] = $query["editid"];
		}

		# Now loop to restore each list
		for ($i=0; $i<count($ids_to_restore); $i++) {

			# Get the indices right
			$index      = $this->FindLineList($ids_to_restore[$i]);
			$otherindex = $otherconfig->FindLineList($ids_to_restore[$i]);

			# Copy the contents and reset the 'modified' flags
			$this->linelists[$index]->commented = $otherconfig->linelists[$otherindex]->commented;
			for ($j=0; $j<15; $j++) {
				$this->linelists[$index]->params[$j] = $otherconfig->linelists[$otherindex]->params[$j];
				$this->linelists[$index]->mod_params[$j] = False;
			}
			$this->linelists[$index]->mod_comment = False;

			# Remove the 'newly_added' flag, since it is now part of the user's configuration
			if (isset($this->linelists[$index]->newly_added)) {
				unset($this->linelists[$index]->newly_added);
			}

		}
	
	}


	function Compare ($otherconfig) {

		# Compare the current linelist against another (default?) linelist
		# No modification of the actual data, except for the 'hidden' parameters, is done here

		# Check and modify the hidden parameters. This can in the future be (re)moved, since
		# we might want to give the user control over these values.
		for ($j=0; $j<count($otherconfig->hiddenparam); $j++) {
			if (!isset($this->hiddenparam[$j])) {
				#print "Adding hidden parameter $j:{$otherconfig->hiddenparam[$j]}<br>\n";
				$this->hiddenparam[$j] = $otherconfig->hiddenparam[$j];
			}
		}

		# Prepare array for lists to delete (one cannot do removal in the loop below, because that
		# would force a change to the loop index counter
		$ids_to_delete = array();

		# Now loop over all lists
		for ($i=0; $i<$this->n_linelists; $i++) {

			# Get the indices right		
			$index      = $this->FindLineList($this->linelists[$i]->id);
			$otherindex = $otherconfig->FindLineList($this->linelists[$i]->id);
			#print "$i-$index-$otherindex.. ";
			
			# If the user's list does not exist in the other linelist, remove it
			# (Removing a list has happened before!)
			if ($otherindex == -1) {
				$ids_to_delete[] = $this->linelists[$index]->id;
				continue;
			}

			# If this lists refers to a filename that is different from the default config,
			# or if the the descriptor of a linelist has changed, then there has evidently
			# been an update to the default linelist. So, remove the entry in the user's list.
			if ( ($this->linelists[$index]->params[0] != $otherconfig->linelists[$otherindex]->params[0]) or
			     ($this->linelists[$index]->name      != $otherconfig->linelists[$otherindex]->name) ) {
				#print "{$this->linelists[$index]->name} ({$this->linelists[$index]->id}) <br>";
				$ids_to_delete[] = $this->linelists[$index]->id;
				continue;
			}

			# Check if the linelist is commented out - report if different		
			if ($this->linelists[$i]->commented != $otherconfig->linelists[$otherindex]->commented) {
				$this->linelists[$i]->mod_comment = True;
			}

			# Check all individual parameters, and report if different
			for ($j=0; $j<14; $j++) {
							
				if ($this->linelists[$i]->params[$j] != $otherconfig->linelists[$otherindex]->params[$j]) {
					$this->linelists[$i]->mod_params[$j] = True;
				}
			}
		}


		# Now delete any linelists that were scheduled for removal
		for ($i=0; $i<count($ids_to_delete); $i++) {
			
			# Get the indices right
			$index      = $this->FindLineList($ids_to_delete[$i]);

			# Now delete the appropriate list
			#print "Deleting list ID {$this->linelists[$index]->id} or {$ids_to_delete[$i]}?}.. \n<br>";
			$this->DeleteLineList($index);

		}


		# Now check if any new list appears in the other (default) linelist...
		# Note, the loop is now over the *other* linelist!
		for ($i=0; $i<$otherconfig->n_linelists; $i++) {
		
			# Get the indices right		
			$index      = $this->FindLineList($otherconfig->linelists[$i]->id);
			$otherindex = $otherconfig->FindLineList($otherconfig->linelists[$i]->id);
			
			#print "$otherindex ({$otherconfig->linelists[$otherindex]->id}) = $index ({$this->linelists[$index]->id}), ";

			# Missing list?						
			if ($index == -1) {

				# If the list is unknown, add it to the user's list
				$this->AddLineList(True, $otherconfig->linelists[$otherindex]->params);
				$newindex = $this->FindLineList($otherconfig->linelists[$i]->id);

				# Copy the contents and reset the 'modified' flags
				$this->linelists[$newindex]->commented = $otherconfig->linelists[$otherindex]->commented;
				for ($j=0; $j<15; $j++) {
					$this->linelists[$newindex]->params[$j] = $otherconfig->linelists[$otherindex]->params[$j];
					$this->linelists[$newindex]->mod_params[$j] = False;
				}

				# And, mark it as a newly added linelist!
				$this->linelists[$newindex]->newly_added = True;
				
				if ($this->linelists[$newindex]->commented != $otherconfig->linelists[$otherindex]->commented) {
					$this->linelists[$newindex]->mod_comment = True;
				}
			}
		
		}
	
	}



}



####################################################################################################################
# 3 - MAIN SECTION
####################################################################################################################

# Force to relocate to Uppsala server
#if ($_SERVER['HTTP_HOST'] != 'vald.astro.uu.se') {
#	header('Location: http://vald.astro.uu.se/~vald/php/vald.php');
#	exit;
#}

# Read configuration files...
#
# First the local config:
include('../../config/site_config_local.php');

# Then the master config:
include('../../config/site_config_master.php');

# Add these config values to the 'replace' array.
$replace = $config;


# Gather all query data, whether submitted with 'POST' or 'GET'
# Use htmlentities to safely escape malicious injected HTML
$QUERY = array();

foreach ($_POST as $key => $value) {
	$QUERY[$key] = htmlentities($value);
}

foreach ($_GET as $key => $value) {
	$QUERY[$key] = htmlentities($value);
}


# Add all extracted keys to the string replacement list
foreach ($QUERY as $key=>$value) {
        $replace[$key] = $value;
        # print "$key = '$value'\n";
}


# Start PHP session, and set caching default
session_cache_limiter('private');
session_start();

# Extract the local svn version number
exec("svnversion ${config['VALD_root']}", $svnversion);

#Construct array for displaying version number
$version_HTML[] = "<h3>Server: ${config['sitename']}</h3>\n";
$version_HTML[] = "<p><a href=\"${config['htmlroot']}/server_status.php\">Version: $svnversion[0]</a>\n";

# Define default frame contents :
$topleft_HTML      = $version_HTML; # file_contents("{$config['html_template_dir']}/", "version.html");
$topright_HTML     = file_contents("{$config['html_template_dir']}/", "topform_not_logged_in.html");
$navigation_HTML   = file_contents("{$config['html_template_dir']}/", "navigate.html");
$content_HTML      = file_contents("{$config['documentation_dir']}", "/about_vald.html");



# Handle logout situation...
if (isset($QUERY['page'])) {	
	if ($QUERY['page'] == 'logout') {

		unset($QUERY['page']);
		unset($QUERY['user']);
		unset($_SESSION);
		session_destroy();
		$content_HTML = file_contents("{$config['html_template_dir']}/", "logout.html");

	}
}


# Handle login situation etc...
if (isset($_SESSION['user'])) {

	# OK - we're dealing with a user in session
	# so let's extract the useful variable(s)

	$user = $_SESSION['user'];
	$user->Validate($config['clients_register']); # Register may have changed since last use...
	$user->Validate($config['clients_register_local']); # Also check the local register

	if ($user->registered) {
		$replace['email']    = $user->email;
		$replace['fullname'] = $user->name;
		$topright_HTML = file_contents("{$config['html_template_dir']}/", "topform_logged_in.html");

		# Extract personal defaults
		foreach ($user->html_defaults as $key => $value) {
			if (!isset($replace[$key])) {		# Only overwrite with user pref if not
				$replace[$key] = $value;	# provided earlier (through form submission)
			}
		}
			

	}

} else {

	# Well, no known user from session data
	# Then, if a username was given - let's try to login

	if (isset($QUERY['user'])) {

		$replace['email'] = $QUERY['user'];
		$user = new User($QUERY['user']);
		$user->Validate($config['clients_register']);

		if (!$user->registered) {
		  $user->Validate($config['clients_register_local']);
		  if ($user->registered) { $user->localuser = True; }
		}
		
		if ($user->registered) {
			$replace['fullname'] = $user->name;
			if ($user->localuser) {
			  $user->persconfig_file = "{$config['persconfig_dir']}/" . trim(preg_replace("/\s+/", "", $user->name)) . ".cfg_local";
			  $user->htmlconfig_file = "{$config['persconfig_dir']}/" . trim(preg_replace("/\s+/", "", $user->name)) . "-HTMLdefs.cfg_local";
			} else {
			  $user->persconfig_file = "{$config['persconfig_dir']}/" . trim(preg_replace("/\s+/", "", $user->name)) . ".cfg";
			  $user->htmlconfig_file = "{$config['persconfig_dir']}/" . trim(preg_replace("/\s+/", "", $user->name)) . "-HTMLdefs.cfg";
			}

			# First read the default settings....
			if (!file_exists($config['htmlconfig_default'])) {
			  	print("<b>ERROR: Cannot open default html configuration file</b>");
			} else {
				$user->ReadHtmlDefs($config['htmlconfig_default']);
			}
			
			# Only to be overwritten by the user's own preferences. This makes it possible to extend the
			# number of options in the future.
			if (file_exists($user->htmlconfig_file)) {
				$user->ReadHtmlDefs($user->htmlconfig_file);
			}
			
			# Now perform a pre-emptive save, in order to absorb any recently added unit definitions
			$user->SaveHtmlDefs($user->htmlconfig_file);

			# Attach all user data to the session variable			
			$_SESSION['user'] = $user;
			$topright_HTML = file_contents("{$config['html_template_dir']}/", "topform_logged_in.html");

			# And make sure the user gets to read the latest news item
			$QUERY['newsitem'] = 0;
		} else {
			$content_HTML = file_contents("{$config['html_template_dir']}/", "notregistered.html");
			session_destroy();
		}

	} else {

		# Unkown user, or no attempt to login...
		# No point in continuing this session.
		if (isset($_SESSION)) { session_destroy(); }
		
		# Create a dummy user (which will be flagged as 'unregistered')
		$user = new User();
	}
}


# Documentation is accessible for all...
if (isset($QUERY["docpage"])) {
	if (file_exists("{$config['documentation_dir']}/{$QUERY['docpage']}")) {
		$docpage = $QUERY['docpage'];
		$content_HTML = file_contents("{$config['documentation_dir']}/", "${docpage}");
	}
}


# Display news items... (also accessible for all)
if (isset($QUERY["newsitem"])) {

	$output_header   = array();
	$output_header[] = "<h4>Latest news from the VALD consortium :</h4>";
	$output_header[] = "<p><hr><p>";

	$filelist = glob("{$config['news_dir']}/[0-9]*", 1);
	$filelist = array_reverse($filelist);

	$output_filelist = array();
	#$output_filelist[] = "<p>More news items : ";
	for ($i=0; $i<count($filelist); $i++) {
		if (($i % 5) == 0) {
			$output_filelist[] = "<p>";
		}
		$output_filelist[] = "<a href=\"\$thisscript?newsitem={$i}\">\n";
		$output_filelist[] = basename($filelist[$i]);
		$output_filelist[] = "</a>,&nbsp;\n";
	}

	$output_filelist[] = "</ul><hr><p>";

	$content_HTML1 = array_merge($output_header);
	$content_HTML2 = array_merge($output_filelist);
	$content_HTML3 = file("{$filelist[$QUERY['newsitem']]}");

	$content_HTML  = array_merge($content_HTML1, $content_HTML2, $content_HTML3);
	
	unset($content_HTML1);
	unset($content_HTML2);
	unset($content_HTML3);

}


# Handle request pages and personal config stuff...
if (isset($QUERY['page'])) {

	# Accessible only for users that are logged-in...
	if (!$user->registered) {
		$error = "You are no longer logged in. Please log in and try again.";
	} else {

		if ($QUERY['page'] == 'saveunits') {

			foreach ($user->html_defaults as $keyword => $dummy) {
				# print "$keyword - ";
				if (isset($QUERY["$keyword"])) {
					$newvalue = $QUERY["$keyword"];
					# print "$newvalue";
					$user->html_defaults[$keyword] = $newvalue;
					$replace[$keyword] = $newvalue;
					$replace["unitsupdated"] = "True";
				}
				# print "<br>\n";
			}

			$user->SaveHtmlDefs(null);
			$content_HTML = file_contents("{$config['html_template_dir']}/", "unitselection.html");

		}

		if ($QUERY['page'] == 'persconf') {
			
			if (!file_exists($config['persconfig_default'])) { 
				print("<b>ERROR: Cannot open default personal configuration file</b>");
			} else {

				$default  = new PersConfig("{$config['persconfig_default']}");
				$persconf = new PersConfig("$user->persconfig_file");

				# If no personal config was found, read the default instead
				if ($persconf->n_linelists == 0) {
					unset($persconf);
					$persconf = new PersConfig("{$config['persconfig_default']}");
					$persconf->filename = $user->persconfig_file;
				}

				$persconf->Compare($default);
				$persconf->SortLineLists();

				if (isset($QUERY['action'])) {
					if ($QUERY['action'] == 'save') {
						$persconf->Update($QUERY);
						$persconf->Compare($default);
					}
					if (($QUERY['action'] == 'restore') or ($QUERY['action'] == 'restore_all')) {
						$persconf->Restore($default, $QUERY);
						$persconf->Compare($default);
					}
				}

				$persconf->WriteFile();

				$content_HTML1 = file_contents("{$config['html_template_dir']}/", "persconf-start.html");
				$content_HTML2 = $persconf->MakeHTML($QUERY);
				$content_HTML3 = file_contents("{$config['html_template_dir']}/", "persconf-end.html");

				$content_HTML = array_merge($content_HTML1, $content_HTML2, $content_HTML3);

				unset($content_HTML1);
				unset($content_HTML2);
				unset($content_HTML3);
			}

		} else {

			if (file_exists("{$config['html_template_dir']}/${QUERY['page']}")) {
				$content_HTML = file_contents("{$config['html_template_dir']}/", "${QUERY['page']}");
			}
		}
	}
}


# Handle submitted requests...
if (isset($QUERY['reqtype'])) {

	if ($QUERY['reqtype'] == 'contact') {
		# Accessible for anybody - this is where we (should) get our new users from...
		$mail_template = file_contents("{$config['request_template_dir']}/", "contact-req.txt");

		# Apply quick spam check
		if ( isset($QUERY['message']) ) {
			if ( SpamCheck($QUERY['message']) ) {

                                $mail_content = "";

				foreach ($mail_template as $line) {
					$mail_content .= EditLine($line, $replace);
				}
				
				if (safe_mail($config["{$QUERY['manager']}"], $QUERY['subject'], $mail_content, $config['send_request_from'])) {
					$content_HTML = file_contents("{$config['html_template_dir']}/", "confirmcontact.html");
				} else {
					$error = "A problem occured when processing your input.";
				}
			} else {
				$error = "Your message was rejected because the content was classed as spam.";
			}
		}
	
	} else {
		# Accessible only for users that are logged-in...
		if ((!$user->registered)) {
			$error = "You are no longer logged in. Please log in and try again.";
		} else {

			if ($QUERY['reqtype'] == 'showline-online') {

				$content_HTML = array();
				
				$req_template = file_contents("{$config['request_template_dir']}/", "showline-online-req.txt");
				$replace["configfile"] = $config['persconfig_default'];
				
				if ($QUERY['pconf'] == 'personal') {
					if (file_exists($user->persconfig_file)) {
						$replace["configfile"] = $user->persconfig_file;
					} else {
						$content_HTML[] = "<b>NOTE: Custom configuration file does not (yet) exist for \$fullname.<br>";
						$content_HTML[] = "Using default configuration instead.</b><p>";
					}
				}
				
				foreach ($req_template as $line) {
					$req_content .= EditLine($line, $replace);
				}
				
				$arguments = " -html";
								
				if (isset($QUERY['isotopic_scaling'])) {
					if ($QUERY['isotopic_scaling'] == 'off') {
						$arguments .= " -noisotopic";
					}
				}
				
				$descriptorspec = array(
				   0 => array("pipe", "r"),  // stdin is a pipe that the child will read from
				   1 => array("pipe", "w"),  // stdout is a pipe that the child will write to
				   2 => array("file", "/tmp/error-output.txt", "a") // stderr is a file to write to
				);

				$process = proc_open("{$config['VALD_root']}/bin/showline4.1" . $arguments, $descriptorspec, $pipes, NULL, NULL);

				if (is_resource($process)) {
					// $pipes now looks like this:
					// 0 => writeable handle connected to child stdin
					// 1 => readable handle connected to child stdout
					// Any error output will be appended to /tmp/error-output.txt

					fwrite($pipes[0], $req_content);
					fclose($pipes[0]);

					while (!feof($pipes[1])) {
						$line = stream_get_line($pipes[1], 10000, "\n");
						$content_HTML[] = "$line\n";
					}
					
					fclose($pipes[1]);

					// It is important that you close any pipes before calling
					// proc_close in order to avoid a deadlock
					$return_value = proc_close($process);
				}
				
			
			} else {
			
				$mail_template = file_contents("{$config['request_template_dir']}/", "${QUERY['reqtype']}-req.txt");

                                $mail_content = "";

				foreach ($mail_template as $line) {
					$mail_content .= EditLine($line, $replace);
				}

				if (safe_mail($config['send_request_to'], $QUERY['subject'], $mail_content, $user->email)) {
					$content_HTML = file_contents("{$config['html_template_dir']}/", "confirmsubmitted.html");
				} else {
					$error = "A problem occured when processing your input\n";
				}
			}
		}
	}
}

# Display error page if necessary...
if (isset($error)) {
	$replace['error'] = $error;
	$content_HTML = file_contents("{$config['html_template_dir']}/", "error.html");
}

# Display maintenance page if necessary...
if (($user->registered) and ($config['maintenance'] == True)) {
	$content_HTML = file_contents("{$config['html_template_dir']}/", "maintenance.html");
}

# Finally, get the stylesheet
$stylesheet = file("{$config['style_file']}");

# Create and output the final HTML page...
MakeAllHTML($stylesheet, $topleft_HTML, $topright_HTML, $navigation_HTML, $content_HTML, $replace);

# That's it!

?>
