#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include "valdems_local.h"
#include "valdems.h"

char *compress(char *s1, char *s)
/*
   Strip all white spaces (blanks, tabulators, new lines
   etc.) and cut out comments.
*/
{
  int i, n;
  char *t;

  t=s1;
  n=(strlen(s)<80)?strlen(s):80;
  for(i=0; i<n; i++)
  {
    if(s[i] == '#') break;
    if(isalnum(s[i]) || s[i]==':' || s[i]=='.' ||
       s[i]==',' || s[i]=='-' || s[i]=='+') *t++ = s[i];
  }
  *t=0;
  return s1;
}

char *str2upper(char *s)
{
  char *t;

  t=s;
  while(*t != '\0')
  {
    *t = toupper(*t); t++;
  }
  return s;
}

char *str2lower(char *s)
{
  char *t;

  t=s;
  while(*t != '\0')
  {
    *t = tolower(*t); t++;
  }
  return s;
}

int swallow_quotes(char *t, char *s, char open_q, char close_q) 
                                     /* Copy s to t character by character. Parts */
{                                    /* of s enclosed in quotes are not copied */
  int quote=0, len=0;

  while(*s!='\0')
  {
    if(*s==open_q && !quote)      quote=1;      /* Encountered opening quote */
    else if(*s==close_q && quote) quote=0;      /* Encountered closing quote */
    else if(!quote)           t[len++]=*s;      /* Copy unquoted text        */
    s++;
  }

  if(quote) len=0;    /* Error if not all the quotes have been closed */
  t[len]='\0';

  return len;
}

int CheckClient(FILE *cl_register, char *address, char *ClientName)
{
  char s[160],s1[160];
  int i, l;

  strcpy(s1, address);
  str2lower(s1);
  l=strlen(s1);
  while(fgets(s, 160, cl_register) != NULL)
  {
    if(s[0]=='#')
    {
      if(s[1]!='$') continue;
      s[0]=' '; s[1]=' ';
      for(i=0; i<strlen(s); i++) if(!isalpha(s[i])) s[i]=' ';
      compress(ClientName, s);
      continue;
    }
    str2lower(s);
    if(!strncmp(s1, s, (l<strlen(s))?l:strlen(s)))
    {
      rewind(cl_register);
      return 1;
    }
  }
  *ClientName=0;
  rewind(cl_register);
  return 0;
}

long get_last_request_ID(void)
{
  FILE *last; long n_requests; int iret; char *filenm, *b, buffer[21];

  filenm=(char *)malloc(strlen(VALD_HOME)+strlen(LAST_SUBMITTED_REQUEST)+1);
  if(filenm==NULL)
  {
    printf("statistics cannot allocate enough memory\n");
    return -1;
  }
  strcpy(filenm, VALD_HOME); strcat(filenm, LAST_SUBMITTED_REQUEST);
  last=fopen(filenm,"rt");
  if(last != NULL)
  {
    b=fgets(buffer, 20, last);
    n_requests=atol(buffer);
    fclose(last);
    free(filenm);
    printf("Last submitted request:%ld\n",n_requests);
  }
  else
  {
    n_requests=-1L;
    printf("PARSEMAIL: Cannot find the last request number file '%s'.\n",filenm);
    free(filenm);
  }

/*
  filenm=(char *)malloc(strlen(VALD_HOME)+strlen("/LOGS/reqID.log")+1);
  strcpy(filenm, VALD_HOME); strcat(filenm, "/LOGS/reqID.log");
  last=fopen(filenm,"at");
  fprintf(last, "Read: %ld\n", n_requests);
  fclose(last);
  free(filenm);
*/

  return n_requests;
}

void put_last_request_ID(long n_requests)
{
  FILE *last; char *filenm;

  filenm=(char *)malloc(strlen(VALD_HOME)+strlen(LAST_SUBMITTED_REQUEST)+1);
  if(filenm==NULL)
  {
    printf("statistics cannot allocate enough memory\n");
    return;
  }
  strcpy(filenm, VALD_HOME); strcat(filenm, LAST_SUBMITTED_REQUEST);
  last=fopen(filenm,"wt");
  fprintf(last, "%ld", n_requests);
  fclose(last);
  free(filenm);


  filenm=(char *)malloc(strlen(VALD_HOME)+strlen("/LOGS/reqID.log")+1);
  strcpy(filenm, VALD_HOME); strcat(filenm, "/LOGS/reqID.log");
  last=fopen(filenm,"at");
  fprintf(last, "Wrote: %ld\n", n_requests);
  fclose(last);
  free(filenm);

}

void main(int n, char *parm[])
{
  long n_requests, n_requests_orig;
  int has_begin_request=0, has_end_request=0, is_mirror=0;
  FILE *fi, *fo, *process, *last, *cl_register, *cl_register_local;
  char s[81], s1[81], filename[81], address[161], ClientName[81],
       *c, *c1, *client_reg, *client_reg_local;

  process=fopen("process","wt");
  fi=fopen(VALD_MAIL,"rt");
  if(fi==NULL)            /* No mail found */
  {
    fclose(process);
    return;
  }
  fputs("#!/bin/csh\n", process);
  fputs("set ERROR_STATE=0\n", process);
  fo=NULL;

  client_reg=(char *)malloc(strlen(VALD_HOME)+strlen(CLIENTS_REGISTER)+1);
  strcpy(client_reg, VALD_HOME); strcat(client_reg, CLIENTS_REGISTER);
  cl_register=fopen(client_reg, "rt");       /* Clients register list
                                                with e-mail addresses */
  if(cl_register==NULL)
  {
    printf("Could not find global client registry file\n");
    printf("Trying to open: %s\n", client_reg);
    /* return; NOT FATAL (YET) */
  }


  /* Same for local version of client register */
  client_reg_local=(char *)malloc(strlen(VALD_HOME)+strlen(CLIENTS_REGISTER_LOCAL)+1);
  strcpy(client_reg_local, VALD_HOME); strcat(client_reg_local, CLIENTS_REGISTER_LOCAL);
  cl_register_local=fopen(client_reg_local, "rt");       /* Clients register list
                                                            with e-mail addresses */
  if(cl_register_local==NULL)
  {
    /* Following can be quiet - server may not have a local client register
    printf("Could not find local client registry file\n");
    printf("Trying to open: %s\n", client_reg_local);
    */
    
    /* return; NOT FATAL (YET) */  }

  if((cl_register==NULL) && (cl_register_local==NULL))
  {
    printf("Could not find any of the client registers!\n");
    return; /* In this situation we return */
  }

  free(client_reg);
  free(client_reg_local);

  n_requests=get_last_request_ID();
  if(n_requests < 0L) n_requests=0L;
  n_requests_orig=n_requests;

/* Here is a weird main loop. It works like this:

  - Read incoming mails line by line.
  - If "From" line is found, check if the previous request was processed and
    the request file closed (fo==NULL)
  - If "From" line has no @ sign, look for "From:" line
  - If previous request was closed check if user is registered, open new
    request file, copy the content of the mail, add lines to the process
    script and so on.
  - If the previous request is still open, check if it had "begin request".
    If not, it might be clients mistake or an info mail for a mirror site.
    In the first case proceed as if it's OK (we let the request processor
    to deal with the error.) The second case is critical as we don't want
    info mails indefinitely bouncing back and forth between the two mirror
    sites so we do not add the reply line into the process script.
*/

  while(fgets(s, 80, fi) != NULL)
  {
    if(!strncmp(s, "From ", 5))
    {                              /* Starting the next mail */
      if(fo != NULL)               /* The previous mail is still open */
      {                            /* We had a legitimate request */
        if(!has_begin_request)
        {                          /* No begin request, kill request */
          fclose(fo);
          n_requests--;
          remove(filename);
          fo=NULL;
          has_begin_request=has_end_request=is_mirror=0;
        }
        else
        {
          fprintf(process, "############## %s #############\n", filename);
          fprintf(process, "echo ============= %s ============ >> %s%s/requests.log\n", filename, VALD_HOME, VALD_LOGS_DIR);
          fprintf(process, "%s%s %s %s", VALD_HOME, PROG_PARSEREQUEST, filename, ClientName);
          fprintf(process, " || (echo ERROR: parserequest failed for request %li; set ERROR_STATE=1)\n", n_requests);
          fprintf(process, "chmod u+x job.%06ld\n", n_requests);
          fprintf(process, "./job.%06ld\n", n_requests);
          fprintf(process, "cat job.%06ld >> %s%s/jobs.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);

#ifdef LOG_DEBUGGING
          fprintf(process, "echo ================== >> %s%s/ems_debug.log\n", VALD_HOME, VALD_LOGS_DIR);
          fprintf(process, "echo Address: %s >> %s%s/ems_debug.log\n", address, VALD_HOME, VALD_LOGS_DIR);
          fprintf(process, "cat process >> %s%s/ems_debug.log\n", VALD_HOME, VALD_LOGS_DIR);
          fprintf(process, "cat request.%06ld >> %s%s/ems_debug.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);
          fprintf(process, "cat job.%06ld >> %s%s/ems_debug.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);
          fprintf(process, "cat result.%06ld >> %s%s/ems_debug.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);
#endif

          fclose(fo);                /* close it and send the reply */
          if(!is_mirror || has_begin_request)
          {                          /* it's not an info mail from a mirror site */
            fprintf(process, "%s %s < result.%06ld", SENDMAIL, address, n_requests);
            fprintf(process, " || (echo ERROR: sendmail failed for request %li; set ERROR_STATE=1)\n", n_requests);
          }
          if(!is_mirror) fprintf(process, "cat %s >> %s%s/requests.log\n", filename, VALD_HOME, VALD_LOGS_DIR);
          else           fprintf(process, "head -20 %s >> %s%s/requests.log\n", filename, VALD_HOME, VALD_LOGS_DIR);
          has_begin_request=has_end_request=is_mirror=0;
          fo=NULL;
        }
      }
      n_requests++;
      sprintf(filename,"request.%06ld", n_requests);
      fo=fopen(filename,"wt");

      *address='\0'; /* Clear address. We will take it later from From: line */
    }
    else if(!strncmp(s, "From: ", 6))
    {
/* Some places (like GSFC) like to put the return address only to the "From:" line.
   Well, what can I say. Instead of going home and having dinner I have to accommodate
   those places too ... 

   The From: line formats that I'm aware of:

   From: name@address
   From: alias ... <name@address>
   From: "alias" <name@address>
   From: name@address (alias)
   From: alias
         <name@address>

   The last format is most ennoying as it can be split between many lines
   if the alias is very long.
*/
      c=strchr(s, '@');
      if(c==NULL)
      {
        while(fgets(s, 80, fi)!=NULL && !strncmp(s, "     ", 5))
	{ 
          if(strchr(s, '@')!=NULL) break;
	}
      }
      swallow_quotes(s1, s+6, '"', '"');    /* Strip quotted aliases */
      swallow_quotes(s1, s1, '(', ')');     /* Strip aliases in parathesis */
      c=strchr(s1, '>');                    /* Make sure we have a terminator */
      c1=strchr(s1, ' ');                   /* Make sure we have a terminator */
      if(c!=NULL) *c='\0'; else if(c1!=NULL) *c1='\0';
      c=strchr(s1, '\n');                   /* Now new line please */
      if(c!=NULL) *c='\0';
      c=strchr(s1, '<');
      if(c!=NULL) strcpy(address, c+1);     /* Clear aliases typical for From: line */
      else        strcpy(address, s1);

                                /* If no apparently valid address would be found */
                                /* the registration check will kill this request */

      c=strrchr(address,'>');
      if(c!=NULL) *c='\0';         /* Clear mail server name if any */
      c=strrchr(address,':');
      if(c!=NULL) strcpy(address,c+1);
      c=strrchr(address,'!');
      if(c!=NULL) strcpy(address,c+1);

      *ClientName=0;
      if(cl_register!=NULL)        /* Check access permission for this client */
      {
        CheckClient(cl_register, address, ClientName);
	/* printf("Ordinary: %s, %s\n", address, ClientName); */
      }

      /* Check local client register if no client found yet*/
      if((*ClientName==0) && (cl_register_local!=NULL))
      {
	CheckClient(cl_register_local, address, ClientName);
	if (*ClientName!=0) { strcat(ClientName, "_local"); }
	/* printf("Secondary: %s, %s\n", address, ClientName); */
      }

      if(*ClientName==0)
      {  /* Client is not in any register, kill request */
         n_requests--;
         remove(filename);
         fo=NULL;
         has_begin_request=has_end_request=is_mirror=0;
         continue;
      }

      if(!strcmp(ClientName, "VALDMirrorSite")) is_mirror=1;
    }
    if(fo != NULL)                 /* Reading the next line from the current request */
    {
      str2lower(compress(s1, s));  /* Strip spaces, convert to lower case */

      if(!strncmp(s1, "beginrequest", strlen("beginrequest"))) has_begin_request=1;

      fputs(s, fo);                /* Copy the line to the request file */

      if(!strncmp(s1, "endrequest", strlen("endrequest"))) has_end_request=1;
    }
  }
  if(fo != NULL)                   /* The previous mail is still open */
  {
    fclose(fo);                    /* close it and send the reply unless */


    if(!has_begin_request)
    {                              /* No begin request, kill request */
      n_requests--;
      remove(filename);
    }
    else
    {

      fprintf(process, "############## %s #############\n", filename);
      fprintf(process, "echo ============= %s ============ >> %s%s/requests.log\n"
                     , filename, VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "%s%s %s %s", VALD_HOME, PROG_PARSEREQUEST, filename, ClientName);
      fprintf(process, " || (echo ERROR: parserequest failed for request %li; set ERROR_STATE=1)\n"
                     , n_requests);
      fprintf(process, "chmod u+x job.%06ld\n", n_requests);
      fprintf(process, "./job.%06ld", n_requests);
      fprintf(process, " || (echo ERROR: job failed for request %li; set ERROR_STATE=1)\n"
                     , n_requests);
      fprintf(process, "cat job.%06ld >> %s%s/jobs.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);

#ifdef LOG_DEBUGGING
      fprintf(process, "echo =================== >> %s%s/ems_debug.log\n", VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "echo Address: %s >> %s%s/ems_debug.log\n", address, VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "cat process >> %s%s/ems_debug.log\n", VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "cat request.%06ld >> %s%s/ems_debug.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "cat job.%06ld >> %s%s/ems_debug.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "cat result.%06ld >> %s%s/ems_debug.log\n", n_requests, VALD_HOME, VALD_LOGS_DIR);
      fprintf(process, "cat numbers %ld %ld >> %s%s/ems_debug.log\n", n_requests_orig, n_requests
                                                                    , VALD_HOME, VALD_LOGS_DIR);
#endif

      if(!is_mirror || has_begin_request)
      {                            /* it's not an info mail from a mirror site */
        fprintf(process, "%s %s < result.%06ld", SENDMAIL, address, n_requests);
        fprintf(process, " || (echo ERROR: sendmail failed for request %li; set ERROR_STATE=1)\n", n_requests);
      }
      if(!is_mirror) fprintf(process, "cat %s >> %s%s/requests.log\n", filename, VALD_HOME, VALD_LOGS_DIR);
      else           fprintf(process, "head -20 %s >> %s%s/requests.log\n", filename, VALD_HOME, VALD_LOGS_DIR);
    }
    fo=NULL;
  }
  fclose(cl_register);
  fputs("exit $ERROR_STATE\n", process);
  fclose(process);
  put_last_request_ID(n_requests);
}
