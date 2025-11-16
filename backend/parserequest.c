#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/types.h>
#include <ctype.h>
/*#include <sys/dirent.h>*/
#include <dirent.h>
#include <sys/stat.h>
#include "valdems_local.h"
#include "valdems.h"

/*===============================================

  VALD-EMS request parser.

  The request is expected in the format:

line 1>    begin request
line 2>    <request type>
line 3>    request itself depending
           on the type
           and as many
           as needed
last line> end request

Example:
--------
begin request
extract stellar
long format
5700.,6700.,
0.01,2.0
8000, 4.5
Sr: -4.67,Cr: -3.37,
Eu: -5.53
end request

  =============================================== */

int PersonalConfiguration=0;
char Client_Name[86];
char Personal_VALD_CONFIG[86];
int LongFormat=0;
int HaveRadiativeDamping=0;
int HaveStarkDamping=0;
int HaveVanderWaalsDamping=0;
int HaveLande=0;
int HaveTermDesignation=0;
int ExtendedWaals=0;
int ZeemanPattern=0;
int StarkBroadening=0;
int FTPretrieval=0;
/* VALD3 retrieval options */
int Energy_in_inv_cm=0;
int Wavelength_in_vac=0;
int Wavelength_units=0; /* 0 - Angstroem, 1 - nm, 2 - cm^-1 */
int Isotopic_scaling_of_gf=1;
int HFS_splitting=0;

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

char *compress(char *s1, char *s, int nn)
/*
   Strip all white spaces (blanks, tabulators, new lines
   etc.) and cut out comments.
*/
{
  int i, n;
  char *t;

  t=s1;
  n=(strlen(s)<nn)?strlen(s):nn;
  for(i=0; i<n; i++)
  {
    if(s[i] == '#') break;
    if(isalnum(s[i]) || s[i]==':' || s[i]=='.' ||
       s[i]==',' || s[i]=='-' || s[i]=='+') *t++ = s[i];
  }
  *t=0;
  return s1;
}

char *compress_species(char *s1, char *s)
/*
   Strip leading and trailing all white spaces (blanks, tabulators, new lines
   etc.), ignore comments, look for one of the four formats:
   <species>
   <species> <number>
   <species>+
   <species>+<number>
   
   where <species> is the species name, e.g. TiO, C2, Fe, C;
         <number> prefixed by space is "spectrum number" (1 - neutral);
	 <number> prefixed by "+" is electric charge.
*/
{
  int i, i1, i2, n, m;
  static char tmp[256];

  n=strlen(s);
  for(i=0; i<n; i++) if(!s[i]=='#') break;     /* Strip comments                      */
  n=(i>255)?255:i;
  tmp[0]='\0';
  for(i=0; i<n; i++) if(!isblank(s[i])) break; /* Skip leading blanks                 */
  if(i==n) return tmp;                         /* Empty string                        */
  i1=i;                                        /* First character in the species name */
  for(; i<n; i++) if(!isalnum(s[i])) break;    /* Species name                        */
  i2=(i<n-1)?i:n-1;                            /* Last character in the species name+1*/
/*  printf("%s %d %d\n",s,i1,i2); */
  strncpy(tmp, s+i1, i2-i1+1);
  m=i2-i1+1;                                   /* Length of the aspecies field        */ 
  for(; i<n; i++) if(!isblank(s[i])) break;    /* Skip trailing blanks                */
  i1=i;                                        /* First character of the charge       */
  for(; i<n; i++) if(!isdigit(s[i])) break;
  i2=i-1;                                      /* Last character of the charge        */
  if(i1<=i2)
  {
    strncpy(tmp+m, s+i1, i2-i1+1);             /* Copy charge                         */
    m+=i2-i1+1;                                /* Add the length of the charge field  */
  }
  tmp[m]='\0';
/*  printf("s=%s %d %d\n",s,i1,i2); 
  printf("tmp=%s %d %d\n",tmp,i1,i2); */
  strcpy(s1, tmp);
  return s1;
}

char *RemoveMeta(char *string)
/* Remove any metashell characters from a string                */
{
   while (strchr(string, ';')) { *strchr(string, ';')  = ' ';};
   while (strchr(string, '&')) { *strchr(string, '&')  = ' ';};
   while (strchr(string, '|')) { *strchr(string, '|')  = ' ';};
   while (strchr(string, '>')) { *strchr(string, '>')  = ' ';};
   while (strchr(string, '<')) { *strchr(string, '<')  = ' ';};
   while (strchr(string, '"')) { *strchr(string, '"')  = ' ';};
   while (strchr(string, '\n')){ *strchr(string, '\n') = ' ';};
   while (strchr(string, '\r')){ *strchr(string, '\r') = ' ';};
   return string;
}

char *SetKeyword(char *s1, FILE *fo, long number)
/* Look up for the keyword and set the corresponding global variable */
{
  /* Long/Short format */
  if(!strncmp(str2upper(s1),"LONGFORMAT",4))
  {
    LongFormat=1; *s1='\0';
  }
  else if(!strncmp(str2upper(s1),"SHORTFORMAT",5))
  {
    LongFormat=0; *s1='\0';
  }

  /* Personal configuration */
  if(!strncmp(str2upper(s1),"PERSONALCONFIGURATION",6))
  {
    DIR *dirp;
    struct dirent *dp;
    char *file, buff[256];
    FILE *fi, *fo1;

    /* Search if Personal configuration exists */
    PersonalConfiguration=1; *s1='\0';
    file=(char *)malloc(strlen(VALD_HOME)+
                        strlen(PERSONAL_CONFIG_DIR)+1);
    if(file==NULL) /* Something is wrong with the path */
    {
      fprintf(fo, "echo ERROR: Wrong path to personal configuration >> result.%06ld\n", number);
      fprintf(fo, "echo        Contact VALD administrator >> result.%06ld\n", number);
      PersonalConfiguration=0;
      return s1;
    }
    strcpy(file, VALD_HOME);
    strcat(file, PERSONAL_CONFIG_DIR);
    dirp=opendir(file);
    free((void *)file);
    while((dp=readdir(dirp))!=NULL)
    {
      if(!strcmp(dp->d_name, Personal_VALD_CONFIG))
      { /* Found */
        closedir(dirp);
        return s1;
      }
    }
    closedir(dirp);
    /* Not found: create one using the default VALD_CONFIG */

    /* Open input file */
    file=(char *)malloc(strlen(VALD_HOME)+
                        strlen(VALD_CONFIG)+1);
    if(file==NULL) /* Something's wrong with path */
    {
      fprintf(fo, "echo ERROR: Wrong path to personal configuration >> result.%06ld\n", number);
      fprintf(fo, "echo        Contact VALD administrator >> result.%06ld\n", number);
      PersonalConfiguration=0;
      *s1='\0';
      return s1;
    }
    strcpy(file, VALD_HOME);
    strcat(file, VALD_CONFIG);
    fi=fopen(file, "rt");
    if(fi==NULL)  /* Something is wrong with the file anyways */
    {
      free((void *)file);
      fprintf(fo, "echo ERROR: Wrong path to personal configuration >> result.%06ld\n", number);
      fprintf(fo, "echo        Contact VALD administrator >> result.%06ld\n", number);
      PersonalConfiguration=0;
      return s1;
    }
    free((void *)file);

    /* Open output file */
    file=(char *)malloc(strlen(VALD_HOME)+
                        strlen(PERSONAL_CONFIG_DIR)+
                        strlen(Personal_VALD_CONFIG)+2);
    if(file==NULL) /* Something's wrong with path */
    {
      fclose(fi);
      fprintf(fo, "echo ERROR: Wrong path to personal configuration >> result.%06ld\n", number);
      fprintf(fo, "echo        Contact VALD administrator >> result.%06ld\n", number);
      PersonalConfiguration=0;
      return s1;
    }
    strcpy(file, VALD_HOME);
    strcat(file, PERSONAL_CONFIG_DIR);
    strcat(file, "/");
    strcat(file, Personal_VALD_CONFIG);
    fo1=fopen(file, "wt");
    if(fo1==NULL)  /* Something is wrong with the file anyways */
    {
      fclose(fi);
      free((void *)file);
      fprintf(fo, "echo ERROR: Wrong path to personal configuration >> result.%06ld\n", number);
      fprintf(fo, "echo        Contact VALD administrator >> result.%06ld\n", number);
      PersonalConfiguration=0;
      return s1;
    }
    while(fgets(buff, 255, fi)!=NULL) fputs(buff, fo1); /* Copy file */
    fclose(fi); fclose(fo1);
    chmod(file, S_IRUSR|S_IWUSR);
    free((void *)file);
    fprintf(fo, "echo Configuration file %s has been created >> result.%06ld\n",
            Personal_VALD_CONFIG, number);
  }

  /* Standard configuration - do nothing */
  if(!strncmp(str2upper(s1),"DEFAULTCONFIGURATION",10))
  {
      PersonalConfiguration=0;
      *s1='\0';
      return s1;
  }

/* HAVE section. This flags allow to extract subset of the normal
   selection for which VALD has explicit values */

  /* Radiative damping */
  if(!strncmp(str2upper(s1),"HAVERAD",7))
  {
    HaveRadiativeDamping=1; *s1='\0';
  }

  /* Stark damping */
  if(!strncmp(str2upper(s1),"HAVESTARK",9))
  {
    HaveStarkDamping=1; *s1='\0';
  }

  /* Van der Waals damping */
  if(!strncmp(str2upper(s1),"HAVEWAALS",9))
  {
    HaveVanderWaalsDamping=1; *s1='\0';
  }

  /* Lande factor */
  if(!strncmp(str2upper(s1),"HAVELANDE",9))
  {
    HaveLande=1; *s1='\0';
  }

  /* Term designations */
  if(!strncmp(str2upper(s1),"HAVETERM",8))
  {
    HaveTermDesignation=1; *s1='\0';
  }


  /* Extended van der Waals */
  if(!strncmp(str2upper(s1),"DEFAULTWAALS",8))
  {
    ExtendedWaals=0; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"EXTENDEDWAALS",9))
  {
    ExtendedWaals=1; *s1='\0';
  }

  /* Zeeman pattern */
  if(!strncmp(str2upper(s1),"ZEEMANPATTERN",6))
  {
    ZeemanPattern=1; *s1='\0';
  }

  /* Stark broadening */
  if(!strncmp(str2upper(s1),"STARKBROADENING",5))
  {
    StarkBroadening=1; *s1='\0';
  }

  /* Retrieve the result via anonymous FTP */
  if(!strncmp(str2upper(s1),"VIAFTP",6))
  {
    FTPretrieval=1; *s1='\0';
  }

  /* Output energes in cm^-1 instead of eV */
  if(!strncmp(str2upper(s1),"ENERGYUNITEV",11))
  {
    Energy_in_inv_cm=0; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"ENERGYUNIT1CM",12))
  {
    Energy_in_inv_cm=1; *s1='\0';
  }

  /* Output vacuum wavelengths */
  if(!strncmp(str2upper(s1),"MEDIUMAIR",7))
  {
    Wavelength_in_vac=0; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"MEDIUMVACUUM",7))
  {
    Wavelength_in_vac=1; *s1='\0';
  }

  /* Wavelength units */
  if(!strncmp(str2upper(s1),"WAVEUNITANGSTROM",9))
  {
    Wavelength_units=0; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"WAVEUNITNM",9))
  {
    Wavelength_units=1; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"WAVEUNIT1CM",10))
  {
    Wavelength_units=2; *s1='\0';
  }

/* Scaling log gf by isotopic ratio */
  if(!strncmp(str2upper(s1),"ISOTOPICSCALINGON",17))
  {
    Isotopic_scaling_of_gf=1; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"ISOTOPICSCALINGOFF",18))
  {
    Isotopic_scaling_of_gf=0; *s1='\0';
  }

/* Insert hyperfine splitting */
  if(!strncmp(str2upper(s1),"HFSSPLITTING",8))
  {
    HFS_splitting=1; *s1='\0';
  }
  if(!strncmp(str2upper(s1),"NOHFSSPLITTING",10))
  {
    HFS_splitting=0; *s1='\0';
  }

  return s1;
}

int GetElementNumber(char *elname)
{
  static char *elements[99]={
                      "H ","HE","LI","BE","B ","C ","N ","O ","F ","NE",
                      "NA","MG","AL","SI","P ","S ","CL","AR","K ","CA",
                      "SC","TI","V ","CR","MN","FE","CO","NI","CU","ZN",
                      "GA","GE","AS","SE","BR","KR","RB","SR","Y ","ZR",
                      "NB","MO","TC","RU","RH","PD","AG","CD","IN","SN",
                      "SB","TE","I ","XE","CS","BA","LA","CE","PR","ND",
                      "PM","SM","EU","GD","TB","DY","HO","ER","TM","YB",
                      "LU","HF","TA","W ","RE","OS","IR","PT","AU","HG",
                      "TL","PB","BI","PO","AT","RN","FR","RA","AC","TH",
                      "PA","U ","NP","PU","AM","CM","BK","CF","ES"};
  int i;

  str2upper(elname);
  for(i=0; i<99; i++)
  {
    if(!strncmp(elname, elements[i], 2))
    {
      if(strlen(elname)>1) elname[1]=tolower(elname[1]);
      return i+1;
    }
  }
  return -1;
}

char *CheckAbund(char *next, char *outs)
{
  double abn;

  if(next==NULL) return next;
  if(next[1]==':')            /* Single character element name */
  {
    outs[0]='\''; outs[1]=next[0]; outs[2]=' '; outs[3]='\0';
    if(GetElementNumber(outs+1)>0)
    {                         /* Element found alright */
      sscanf(next+2, "%lg", &abn);
      sprintf(outs+2, ":%.2f\',", abn);
      next=strchr(next, ',');
      if(next!=NULL)
      {
        next++;
        if(*next=='\0') next=NULL;
      }
      return next;
    }
  }
  else if(!strncmp(next, "MH:", 3) || !strncmp(next, "m/h:", 4))
  {
    sscanf(next+3, "%lg", &abn);
    sprintf(outs, "\'M/H:%.2f\',", abn);
    next=strchr(next, ',');
    if(next!=NULL)
    {
      next++;
      if(*next=='\0') next=NULL;
    }
    return next;
  }
  else if(next[2]==':')       /* Double character element name */
  {
    outs[0]='\''; outs[1]=next[0]; outs[2]=next[1]; outs[3]='\0';
    if(GetElementNumber(outs+1)>0)
    {                         /* Element found alright */
      sscanf(next+3, "%lg", &abn);
      sprintf(outs+3, ":%.2f\',", abn);
      next=strchr(next, ',');
      if(next!=NULL)
      {
        next++;
        if(*next=='\0') next=NULL;
      }
      return next;
    }
  }
                              /* Unknown element name */
  strncpy(outs, next, 3); outs[3]='\0';
  next=strchr(next, ','); if(next!=NULL) next++;
  return next;
}

int FindNearestModel(char *dirname, char *name, char *bestmod)
/*
   Check directory "dirname" for the model atmosphere closest
   to the one specified by "name". The information about Teff
   and log g is coded into the filename as, for example:
   05500g35.krz
   Teff  Gr
   so only filenames are checked. "name" must have the same structure.
   Teff has more weight over gravity. The best guess is returned
   in "bestmod". If no model has been found, strlen(bestname)==0 and
   FindNearestModel returns 0, otherwise it returns 1.
*/
{
  DIR *dirp;
  struct dirent *dp;
  int i, len;
  int Teff, teff, tbest, Logg, logg, gbest;

  sscanf(name, MODEL_NAME_FORMAT, &Teff, &Logg); /* Parse name */
  tbest=-1; gbest=-1;
  dirp=opendir(dirname);
  for(dp=readdir(dirp); dp!=NULL; dp=readdir(dirp))
  {                                         /* Make sure it's a model */
    if(sscanf(dp->d_name, MODEL_NAME_FORMAT, &teff, &logg)==2)
    {                                     /* Parse name */
      if(abs(teff-Teff)<abs(tbest-Teff))
      {                                   /* Closer temperature */
        tbest=teff; gbest=logg;
      }
      else if(abs(teff-Teff)==abs(tbest-Teff) &&
              abs(logg-Logg)< abs(gbest-Logg))
      {                          /* Same temperature but better gravity */
        tbest=teff; gbest=logg;
      }
    }
  }
  closedir(dirp);
  if(tbest>=0 && gbest>=0)
  {
    sprintf(bestmod, MODEL_NAME_FORMAT, tbest, gbest);
    return 1;
  }
  else
  {
    bestmod[0]='\0';
    return 0;
  }
}

int ShowLine(FILE *fi, FILE *fo, long number)
{
  int elnum, ishow, has_elem, end_of_req;
  FILE *fo1;
  char show_in[81], s[81], s1[81], s2[81], HFS_switch[6];
  double wlcenter, wlwindow;

  ishow=-1;
  while(1)    /* Many showlines in one request */
  {
    ishow++;
    wlcenter= -1;
    fprintf(fo, "echo  =============================================================================== >> result.%06ld\n",
            number);
    sprintf(show_in, "show_in.%06ld_%03d", number, ishow);
    end_of_req=1;
    while(fgets(s, 80, fi) != NULL)     /* Read wavelength range */
    {
      end_of_req=0;                     /* It's not the last line of request */
      compress(s1, s, 80);
      SetKeyword(s1, fo, number);
      if(!strncmp(str2lower(s1), "endrequest", 10)) return EXIT_SUCCESS;
      if(strlen(s1) == 0) continue;
/*      compress_species(s1, s); */
      if(!isdigit(*s1) && *s1 != '.' && *s1 != '+' && *s1 != '-')
      {
        fprintf(fo, "echo WARNING: Unknown option: %s (ignored) >> result.%06ld\n",
                s1,number);
        continue;
      }
      if(sscanf(s1, "%lg%*1s%lg", &wlcenter, &wlwindow)!=2)
      {
        wlcenter=-1;
        break;
      }
      fo1=fopen(show_in,"wt");
      fprintf(fo1, "%lg,%lg\n", wlcenter, wlwindow);
      break;
    }
    if(wlcenter<0)
    {
      if(end_of_req) return EXIT_SUCCESS;
      fprintf(fo, "echo WARNING: Cannot read central wavelength and scan window (entry ignored) >> result.%06ld\n",
              number);
      continue;
    }

    has_elem=0;
    end_of_req=1;
    if(HFS_splitting) strncpy(HFS_switch, " -HFS", 5); else HFS_switch[0]='\0';
    while(fgets(s, 80, fi) != NULL)
    {
      char elm[2];

      end_of_req=0;
      strcpy(s1,"                                 ");
      compress_species(s1, s);
      strcpy(s2, s1);
      SetKeyword(s2, fo, number);
      if(!strncmp(str2lower(s), "endrequest", 10)) return EXIT_SUCCESS;
      if(strlen(s1) == 0) continue;
      has_elem++;
        if(PersonalConfiguration)
        {
          fprintf(fo1, "%s\n%s%s/%s\n", s1, VALD_HOME, PERSONAL_CONFIG_DIR,
                  Personal_VALD_CONFIG);
        }
        else
        {
          fprintf(fo1, "%s\n%s%s\n", s1, VALD_HOME, VALD_CONFIG);
        }
        fclose(fo1);
        if(Isotopic_scaling_of_gf)
        {
          fprintf(fo, "(%s%s%s) < %s | ((%s%s 10) >> result.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_SHOWLINE, HFS_switch, show_in,
                  VALD_HOME, PROG_SWALLOW, number);
        }
        else
        {
          fprintf(fo, "%s%s -noisotopic < %s | ((%s%s 10) >> result.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_SHOWLINE, show_in,
                  VALD_HOME, PROG_SWALLOW, number);
        }
        fprintf(fo, "rm %s\n", show_in);
        break;
    }
    if(!has_elem)
    {
      fprintf(fo, "rm %s\n", show_in);
      fprintf(fo,"echo WARNING: Element name is missing (ignored) >> result.%06ld\n",
              number);
    }
    fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
  }
  return EXIT_SUCCESS;
}

int ExtractAll(FILE *fi, FILE *fo, long number)
{
  FILE *fo1;
  char pres_in[81], s[81], s1[81];
  double wlleft, wlright;

  wlright= -1;
  sprintf(pres_in, "pres_in.%06ld", number);
  while(fgets(s, 80, fi) != NULL)     /* Read wavelength range */
  {
    compress(s1, s, 80);
    SetKeyword(s1, fo, number);
    if(strlen(s1) == 0) continue;
    if(!isdigit(*s1) && *s1 != '.' && *s1 != '+' && *s1 != '-')
    {
      fprintf(fo, "echo WARNING: Unknown option: %s (ignored) >> result.%06ld\n",
              s1,number);
      continue;
    }
    if(sscanf(s1, "%lg%*1s%lg", &wlleft, &wlright)!=2)
    {
      fprintf(fo,"echo FAILURE: Cannot read wavelength range >> result.%06ld\n",
              number);
      return EXIT_FAILURE;
    }
    else if(wlleft > wlright || wlleft <= 0)
    {
      fprintf(fo,"echo FAILURE: Bad wavelength range >> result.%06ld\n",
              number);
      return EXIT_FAILURE;
    }
    fo1=fopen(pres_in,"wt");
    if(FTPretrieval)
    {                  /* Allow more lines per one request via FTP */
      fprintf(fo1, "%lg,%lg\n%d\n", wlleft, wlright, MAX_LINES_PER_FTP);
    }
    else
    {
      fprintf(fo1, "%lg,%lg\n%d\n", wlleft, wlright, MAX_LINES_PER_REQUEST);
    }
    if(PersonalConfiguration)
    {
      fprintf(fo1, "\n\'%s%s/%s\'\n%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
              VALD_HOME, PERSONAL_CONFIG_DIR, Personal_VALD_CONFIG,
              LongFormat+3*Energy_in_inv_cm, HaveRadiativeDamping,
              HaveStarkDamping, HaveVanderWaalsDamping, HaveLande,
              HaveTermDesignation, ExtendedWaals, ZeemanPattern,
              StarkBroadening, Wavelength_in_vac, Wavelength_units,
              Isotopic_scaling_of_gf, HFS_splitting);
    }
    else
    {
      fprintf(fo1, "\n\'%s%s\'\n%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
              VALD_HOME, VALD_CONFIG,
              LongFormat+3*Energy_in_inv_cm, HaveRadiativeDamping,
              HaveStarkDamping, HaveVanderWaalsDamping, HaveLande,
              HaveTermDesignation, ExtendedWaals, ZeemanPattern,
              StarkBroadening, Wavelength_in_vac, Wavelength_units,
              Isotopic_scaling_of_gf, HFS_splitting);
    }
    fclose(fo1);

    if(HFS_splitting) /* Perform hyperfine splitting */
    {
      if(FTPretrieval)
      {
        fprintf(fo, "%s%s < %s | %s%s | %s%s | (%s%s > %s.%06ld) >>& err.log\n",
                VALD_HOME, PROG_PRESELECT, pres_in,
                VALD_HOME, PROG_FORMAT,
                VALD_HOME, PROG_HFS_SPLIT,
                VALD_HOME, PROG_POST_HFS_FORMAT,
                Client_Name, number);
        fprintf(fo, "gzip %s.%06ld\n", Client_Name, number);
        fprintf(fo, "mv %s.%06ld.gz %s\n", Client_Name, number, VALD_FTP_DIR);
        fprintf(fo, "chmod a+r %s/%s.%06ld.gz\n", VALD_FTP_DIR, Client_Name, number);
        fprintf(fo, "mv %s %s.%06ld.bib\n", POST_HFS_BIB_FILE,
                                           Client_Name, number);
        fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
        fprintf(fo, "mv %s.%06ld.bib.gz %s\n", Client_Name, number, VALD_FTP_DIR);
        fprintf(fo, "chmod a+r %s/%s.%06ld.bib.gz\n", VALD_FTP_DIR, Client_Name, number);
        fprintf(fo, "echo VALD processed your request number %ld >> result.%06ld\n",
                number, number);
        fprintf(fo, "echo Results can be retrieved with a Web browser at >> result.%06ld\n",
                number);
        fprintf(fo, "echo %s/%s.%06ld.gz >> result.%06ld\n",
                VALD_FTP, Client_Name, number, number);
        fprintf(fo, "echo %s/%s.%06ld.bib.gz >> result.%06ld\n",
                VALD_FTP, Client_Name, number, number);
        fprintf(fo, "echo This link will be valid for 48 hours only >> result.%06ld\n",
                number);
     /* fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number); */
      }
      else
      {
        fprintf(fo, "%s%s < %s | %s%s | %s%s | (%s%s >> result.%06ld) >>& err.log\n",
                VALD_HOME, PROG_PRESELECT, pres_in,
                VALD_HOME, PROG_FORMAT,
                VALD_HOME, PROG_HFS_SPLIT,
                VALD_HOME, PROG_POST_HFS_FORMAT,
                number);
        fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
        fprintf(fo, "mv %s %s.%06ld.bib\n", POST_HFS_BIB_FILE,
                                         Client_Name, number);
        fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
        fprintf(fo,"echo \"Content-Disposition: attachment; filename=%s.%06ld.bib.gz;\" >> result.%06ld\n",
                Client_Name, number, number);
        fprintf(fo,"echo \"Content-Type: application/octet-stream\" >> result.%06ld\n", number);
        fprintf(fo,"echo \"Content-Transfer-Encoding: base64\" >> result.%06ld\n", number);
        fprintf(fo,"echo \"\" >> result.%06ld\n", number);
        fprintf(fo, "%s %s.%06ld.bib.gz >> result.%06ld\n", BASE64, Client_Name, number, number);
        fprintf(fo,"echo \"--===MailSection==--\" >> result.%06ld\n", number);
        fprintf(fo, "rm %s.%06ld.bib.gz\n", Client_Name, number);
      }
    }
    else             /* Old fashion, no HFS */
    {
      if(FTPretrieval)
      {
        fprintf(fo, "%s%s < %s | (%s%s > %s.%06ld) >>& err.log\n",
                VALD_HOME, PROG_PRESELECT, pres_in,
                VALD_HOME, PROG_FORMAT, Client_Name, number);
        fprintf(fo, "gzip %s.%06ld\n", Client_Name, number);
        fprintf(fo, "mv %s.%06ld.gz %s\n", Client_Name, number, VALD_FTP_DIR);
        fprintf(fo, "chmod a+r %s/%s.%06ld.gz\n", VALD_FTP_DIR, Client_Name, number);
        fprintf(fo, "mv %s %s.%06ld.bib\n", PRESFORMAT_BIB_FILE,
                                           Client_Name, number);
        fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
        fprintf(fo, "mv %s.%06ld.bib.gz %s\n", Client_Name, number, VALD_FTP_DIR);
        fprintf(fo, "chmod a+r %s/%s.%06ld.bib.gz\n", VALD_FTP_DIR, Client_Name, number);
        fprintf(fo, "echo VALD processed your request number %ld >> result.%06ld\n",
                number, number);
        fprintf(fo, "echo Results can be retrieved with a Web browser at >> result.%06ld\n",
                number);
        fprintf(fo, "echo %s/%s.%06ld.gz >> result.%06ld\n",
                VALD_FTP, Client_Name, number, number);
        fprintf(fo, "echo %s/%s.%06ld.bib.gz >> result.%06ld\n",
                VALD_FTP, Client_Name, number, number);
        fprintf(fo, "echo This link will be valid for 48 hours only >> result.%06ld\n",
                number);
     /* fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number); */
      }
      else
      {
          fprintf(fo, "%s%s < %s | (%s%s >> result.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_PRESELECT, pres_in,
                  VALD_HOME, PROG_FORMAT, number);
        fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
        fprintf(fo, "mv %s %s.%06ld.bib\n", PRESFORMAT_BIB_FILE,
                                         Client_Name, number);
        fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
        fprintf(fo,"echo \"Content-Disposition: attachment; filename=%s.%06ld.bib.gz;\" >> result.%06ld\n",
                Client_Name, number, number);
        fprintf(fo,"echo \"Content-Type: application/octet-stream\" >> result.%06ld\n", number);
        fprintf(fo,"echo \"Content-Transfer-Encoding: base64\" >> result.%06ld\n", number);
        fprintf(fo,"echo \"\" >> result.%06ld\n", number);
        fprintf(fo, "%s %s.%06ld.bib.gz >> result.%06ld\n", BASE64, Client_Name, number, number);
        fprintf(fo,"echo \"--===MailSection==--\" >> result.%06ld\n", number);
        fprintf(fo, "rm %s.%06ld.bib.gz\n", Client_Name, number);
      }
    }
    fprintf(fo, "rm %s\n", pres_in);
    return EXIT_SUCCESS;
  }
  fprintf(fo,"echo FAILURE: Cannot read wavelength range >> result.%06ld\n",
          number);
/*  fprintf(fo,"echo \"--==MailSection==--\" >> result.%06ld\n", number); */
  fprintf(fo, "rm %s\n", pres_in);
  return EXIT_FAILURE;
}

int ExtractElement(FILE *fi, FILE *fo, long number)
{
  int elnum;
  FILE *fo1;
  char pres_in[81], s[81], s1[81];
  double wlleft, wlright;

  wlright= -1;
  sprintf(pres_in, "pres_in.%06ld", number);
  while(fgets(s, 80, fi) != NULL)     /* Read wavelength range */
  {
    compress(s1, s, 80);
    SetKeyword(s1, fo, number);
    if(strlen(s1) == 0) continue;
    if(!isdigit(*s1) && *s1 != '.' && *s1 != '+' && *s1 != '-')
    {
      fprintf(fo, "echo WARNING: Unknown option: %s (ignored) >> result.%06ld\n",
              s1,number);
      continue;
    }
    if(sscanf(s1, "%lg%*1s%lg", &wlleft, &wlright)!=2)
    {
      fprintf(fo,"echo FAILURE: Cannot read wavelength range >> result.%06ld\n",
              number);
      return EXIT_FAILURE;
    }
    else if(wlleft > wlright || wlleft <= 0)
    {
      fprintf(fo,"echo FAILURE: Bad wavelength range >> result.%06ld\n",
              number);
      return EXIT_FAILURE;
    }
    fo1=fopen(pres_in,"wt");
    if(FTPretrieval)
    {           /* Allow more lines to be retrieved via FTP */
      fprintf(fo1, "%lg,%lg\n%d\n", wlleft, wlright, MAX_LINES_PER_FTP);
    }
    else
    {
      fprintf(fo1, "%lg,%lg\n%d\n", wlleft, wlright, MAX_LINES_PER_REQUEST);
    }
    break;
  }
  if(wlright<0)
  {
    fprintf(fo,"echo FAILURE: Cannot read wavelength range >> result.%06ld\n",
            number);
    return EXIT_FAILURE;
  }

  while(fgets(s, 80, fi) != NULL)
  {
    char elm[2];

    compress_species(s1, s);
    if(strlen(s1) == 0) continue;
      if(PersonalConfiguration)
      {
        fprintf(fo1, "%s\n\'%s%s/%s\'\n%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
                s1, VALD_HOME, PERSONAL_CONFIG_DIR, Personal_VALD_CONFIG,
                LongFormat+3*Energy_in_inv_cm, HaveRadiativeDamping,
                HaveStarkDamping, HaveVanderWaalsDamping, HaveLande,
                HaveTermDesignation, ExtendedWaals, ZeemanPattern,
                StarkBroadening, Wavelength_in_vac, Wavelength_units,
                Isotopic_scaling_of_gf, HFS_splitting);
      }
      else
      {
        fprintf(fo1, "%s\n\'%s%s\'\n%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
                s1, VALD_HOME, VALD_CONFIG,
                LongFormat+3*Energy_in_inv_cm, HaveRadiativeDamping,
                HaveStarkDamping, HaveVanderWaalsDamping, HaveLande,
                HaveTermDesignation, ExtendedWaals, ZeemanPattern,
                StarkBroadening, Wavelength_in_vac, Wavelength_units,
                Isotopic_scaling_of_gf, HFS_splitting);
      }
      fclose(fo1);

      if(HFS_splitting) /* Configure hyperfine splitting */
      {
        if(FTPretrieval)
        {
          fprintf(fo, "%s%s < %s | %s%s | %s%s | (%s%s > %s.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_PRESELECT, pres_in,
                  VALD_HOME, PROG_FORMAT,
                  VALD_HOME, PROG_HFS_SPLIT,
                  VALD_HOME, PROG_POST_HFS_FORMAT,
                  Client_Name, number);
          fprintf(fo, "gzip %s.%06ld\n", Client_Name, number);
          fprintf(fo, "mv %s.%06ld.gz %s\n", Client_Name, number, VALD_FTP_DIR);
          fprintf(fo, "chmod a+r %s/%s.%06ld.gz\n", VALD_FTP_DIR, Client_Name, number);
          fprintf(fo, "mv %s %s.%06ld.bib\n", POST_HFS_BIB_FILE,
                                             Client_Name, number);
          fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
          fprintf(fo, "mv %s.%06ld.bib.gz %s\n", Client_Name, number, VALD_FTP_DIR);
          fprintf(fo, "chmod a+r %s/%s.%06ld.bib.gz\n", VALD_FTP_DIR, Client_Name, number);
          fprintf(fo, "echo VALD processed your request number %ld >> result.%06ld\n",
                  number, number);
          fprintf(fo, "echo Results can be retrieved with a Web browser at >> result.%06ld\n",
                  number);
          fprintf(fo, "echo %s/%s.%06ld.gz >> result.%06ld\n",
                  VALD_FTP, Client_Name, number, number);
          fprintf(fo, "echo %s/%s.%06ld.bib.gz >> result.%06ld\n",
                  VALD_FTP, Client_Name, number, number);
          fprintf(fo, "echo This link will be valid for 48 hours only >> result.%06ld\n",
                  number);
       /* fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number); */
        }
        else
        {
          fprintf(fo, "%s%s < %s | %s%s | %s%s | (%s%s >> result.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_PRESELECT, pres_in,
                  VALD_HOME, PROG_FORMAT,
                  VALD_HOME, PROG_HFS_SPLIT,
                  VALD_HOME, PROG_POST_HFS_FORMAT,
                  number);
          fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
          fprintf(fo, "mv %s %s.%06ld.bib\n", POST_HFS_BIB_FILE,
                                           Client_Name, number);
          fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
          fprintf(fo,"echo \"Content-Disposition: attachment; filename=%s.%06ld.bib.gz;\" >> result.%06ld\n",
                  Client_Name, number, number);
          fprintf(fo,"echo \"Content-Type: application/octet-stream\" >> result.%06ld\n", number);
          fprintf(fo,"echo \"Content-Transfer-Encoding: base64\" >> result.%06ld\n", number);
          fprintf(fo,"echo \"\" >> result.%06ld\n", number);
          fprintf(fo, "%s %s.%06ld.bib.gz >> result.%06ld\n", BASE64, Client_Name, number, number);
          fprintf(fo,"echo \"--===MailSection==--\" >> result.%06ld\n", number);
          fprintf(fo, "rm %s.%06ld.bib.gz\n", Client_Name, number);
        }
      }
      else       /* Old fashion, no HFS */
      {
        if(FTPretrieval)
        {
          fprintf(fo, "%s%s < %s | (%s%s > %s.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_PRESELECT, pres_in,
                  VALD_HOME, PROG_FORMAT, Client_Name, number);
          fprintf(fo, "gzip %s.%06ld\n", Client_Name, number);
          fprintf(fo, "mv %s.%06ld.gz %s\n", Client_Name, number, VALD_FTP_DIR);
          fprintf(fo, "chmod a+r %s/%s.%06ld.gz\n", VALD_FTP_DIR, Client_Name, number);
          fprintf(fo, "mv %s %s.%06ld.bib\n", PRESFORMAT_BIB_FILE,
                                             Client_Name, number);
          fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
          fprintf(fo, "mv %s.%06ld.bib.gz %s\n", Client_Name, number, VALD_FTP_DIR);
          fprintf(fo, "chmod a+r %s/%s.%06ld.bib.gz\n", VALD_FTP_DIR, Client_Name, number);
          fprintf(fo, "echo VALD processed your request number %ld >> result.%06ld\n",
                  number, number);
          fprintf(fo, "echo Results can be retrieved with a Web browser at >> result.%06ld\n",
                  number);
          fprintf(fo, "echo %s/%s.%06ld.gz >> result.%06ld\n",
                  VALD_FTP, Client_Name, number, number);
          fprintf(fo, "echo %s/%s.%06ld.bib.gz >> result.%06ld\n",
                  VALD_FTP, Client_Name, number, number);
          fprintf(fo, "echo This link will be valid for 48 hours only >> result.%06ld\n",
                  number);
       /* fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number); */
        }
        else
        {
          fprintf(fo, "%s%s < %s | (%s%s >> result.%06ld) >>& err.log\n",
                  VALD_HOME, PROG_PRESELECT, pres_in,
                  VALD_HOME, PROG_FORMAT, number);
          fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
          fprintf(fo, "mv %s %s.%06ld.bib\n", PRESFORMAT_BIB_FILE,
                                           Client_Name, number);
          fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
          fprintf(fo,"echo \"Content-Disposition: attachment; filename=%s.%06ld.bib.gz;\" >> result.%06ld\n",
                  Client_Name, number, number);
          fprintf(fo,"echo \"Content-Type: application/octet-stream\" >> result.%06ld\n", number);
          fprintf(fo,"echo \"Content-Transfer-Encoding: base64\" >> result.%06ld\n", number);
          fprintf(fo,"echo \"\" >> result.%06ld\n", number);
          fprintf(fo, "%s %s.%06ld.bib.gz >> result.%06ld\n", BASE64, Client_Name, number, number);
          fprintf(fo,"echo \"--===MailSection==--\" >> result.%06ld\n", number);
          fprintf(fo, "rm %s.%06ld.bib.gz\n", Client_Name, number);
        }
      }
      fprintf(fo, "rm %s\n", pres_in);
      return EXIT_SUCCESS;
  }
  fprintf(fo, "rm %s\n", pres_in);
  fprintf(fo,"echo FAILURE: Element name is missing >> result.%06ld\n",
          number);
/*  fprintf(fo,"echo \"--==MailSection==--\" >> result.%06ld\n", number); */
  return EXIT_FAILURE;
}

int ExtractStellar(FILE *fi, FILE *fo, long number)
{
  int iteff, log_g, i;
  FILE *fo1;
  char pres_in[81], s[321], s1[321], model[81], *model_dir, bestmodel[81];
  double wlleft, wlright, criter, vmicro, teff, grav;

  wlright= -1;
  sprintf(pres_in, "pres_in.%06ld", number);
  while(fgets(s, 80, fi) != NULL)     /* Read wavelength range */
  {
    compress(s1, s, 80);
    SetKeyword(s1, fo, number);
    if(strlen(s1) == 0) continue;
    if(!isdigit(*s1) && *s1 != '.' && *s1 != '+' && *s1 != '-')
    {
      fprintf(fo, "echo FAILURE: Unknown option: %s >> result.%06ld\n",
              s1,number);
      continue;
    }
    if(sscanf(s1, "%lg%*1s%lg", &wlleft, &wlright)!=2)
    {
      fprintf(fo,"echo FAILURE: Cannot read wavelength range >> result.%06ld\n",
              number);
      return EXIT_FAILURE;
    }
    else if(wlleft > wlright || wlleft <= 0)
    {
      fprintf(fo,"echo FAILURE: Bad wavelength range >> result.%06ld\n",
              number);
      return EXIT_FAILURE;
    }
    fo1=fopen(pres_in,"wt");
    fprintf(fo1, "%lg,%lg\n0\n", wlleft, wlright);
    if(PersonalConfiguration)
    {
      fprintf(fo1, "\n\'%s%s/%s\'\n%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
              VALD_HOME, PERSONAL_CONFIG_DIR, Personal_VALD_CONFIG,
              LongFormat+3*Energy_in_inv_cm, HaveRadiativeDamping,
              HaveStarkDamping, HaveVanderWaalsDamping, HaveLande,
              HaveTermDesignation, ExtendedWaals, ZeemanPattern,
              StarkBroadening, Wavelength_in_vac, Wavelength_units,
              Isotopic_scaling_of_gf, HFS_splitting);
    }
    else
    {
      fprintf(fo1, "\n\'%s%s\'\n%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
              VALD_HOME, VALD_CONFIG,
              LongFormat+3*Energy_in_inv_cm, HaveRadiativeDamping,
              HaveStarkDamping, HaveVanderWaalsDamping, HaveLande,
              HaveTermDesignation, ExtendedWaals, ZeemanPattern,
              StarkBroadening, Wavelength_in_vac, Wavelength_units,
              Isotopic_scaling_of_gf, HFS_splitting);
    }
    fclose(fo1);
    break;
  }
  if(wlright<0)
  {
    fprintf(fo,"echo FAILURE: Cannot read wavelength range >> result.%06ld\n",
            number);
    fprintf(fo, "rm %s\n", pres_in);
    return EXIT_FAILURE;
  }

  vmicro= -1;
  fo1=fopen("select.input","wt");
  while(fgets(s, 80, fi) != NULL)     /* Read criterion and Vmicro */
  {
    compress(s1, s, 80);
    SetKeyword(s1, fo, number);
    if(strlen(s1) == 0) continue;
    if(!isdigit(*s1) && *s1 != '.' && *s1 != '+' && *s1 != '-')
    {
      fprintf(fo, "echo FAILURE: Unknown option: %s >> result.%06ld\n",
              s1,number);
      continue;
    }
    if(sscanf(s1, "%lg%*1s%lg", &criter, &vmicro)!=2)
    {
      fprintf(fo,"echo FAILURE: Cannot read criterion and Vmicro >> result.%06ld\n",
              number);
      fprintf(fo, "rm %s\n", pres_in);
      return EXIT_FAILURE;
    }
    fprintf(fo1, "%lg,%lg,%lg,%lg\n", wlleft, wlright,criter,vmicro);
    break;
  }
  if(vmicro<0)
  {
    fprintf(fo,"echo FAILURE: Cannot read criterion and Vmicro >> result.%06ld\n",
            number);
    fprintf(fo, "rm %s\n", pres_in);
    return EXIT_FAILURE;
  }

  teff= -1;
  while(fgets(s, 80, fi) != NULL)     /* Read Teff and gravity */
  {
    compress(s1, s, 80);
    SetKeyword(s1, fo, number);
    if(strlen(s1) == 0) continue;
    if(!isdigit(*s1) && *s1 != '.' && *s1 != '+' && *s1 != '-')
    {
      fprintf(fo, "echo FAILURE: Unknown option: %s >> result.%06ld\n",
              s1,number);
      continue;
    }
    if(sscanf(s1, "%lg%*1s%lg", &teff, &grav)!=2)
    {
      fprintf(fo,"echo FAILURE: Cannot read Teff and gravity >> result.%06ld\n",
              number);
      fprintf(fo, "rm %s\n", pres_in);
      return EXIT_FAILURE;
    }
    iteff=teff;    if(((int)(teff*10))%10>5) iteff++;
    log_g=grav*10; if(((int)(grav*100))%10>5) log_g++;
    sprintf(model, MODEL_NAME_FORMAT, iteff, log_g);
    model_dir=(char *)malloc(strlen(VALD_HOME)+strlen(DIR_MODELS)+1);
    if(model_dir==NULL)
    {
      fprintf(fo,"echo FAILURE: VALD request parser could not allocate memory\n");
      return EXIT_FAILURE;
    }
    strcpy(model_dir, VALD_HOME); strcat(model_dir, DIR_MODELS);
    if(!FindNearestModel(model_dir, model, bestmodel))
    {
      fprintf(fo,"echo FAILURE: VALD could not find any atmosphere model>> result.%06ld\n",
              number);
      fprintf(fo, "rm %s\n", pres_in);
      free((void *)model_dir);
      return EXIT_FAILURE;
    }
    else if(strcmp(bestmodel, model))
    {
      fprintf(fo,"echo WARNING: VALD does not have the exact model, will use %s instead >> result.%06ld\n",
              bestmodel, number);
    }
    free((void *)model_dir);
    fprintf(fo1, "\'%s%s/%s\'\n", VALD_HOME, DIR_MODELS, bestmodel);
    break;
  }
  if(teff<0)
  {
    fprintf(fo,"echo FAILURE: Cannot read Teff and gravity >> result.%06ld\n",
            number);
    fprintf(fo, "rm %s\n", pres_in);
    return EXIT_FAILURE;
  }

  i=0;
  while(fgets(s, 320, fi) != NULL)     /* Read abundances */
  {
    char *next, outs[13];

    compress(s1, s, 320);
    SetKeyword(s1, fo, number);
    if(strlen(s1) == 0) continue;
    if(!strncmp(s1, "ENDREQUEST", strlen("ENDREQUEST"))) break;
    next=s1;
    while(next!=NULL)
    {
      next=CheckAbund(next, outs);
      if(strlen(outs)>=5)
      {                              /* Element check OK */
        if(i>66)
        {
          fputs("\n", fo1); i=0;
        }
        fputs(outs, fo1); i+= strlen(outs);
      }
      else                           /* Wrong element name */
      {
        fprintf(fo,"echo WARNING: Never heard of element: %s >> result.%06ld\n",
                outs, number);
      }
    }
  }
  if(i>66) fputs("\n", fo1);
  fputs("\'END\'\n", fo1);
  fputs("\'Synth\'\n", fo1);
  fputs("\'select.out\'\n", fo1);
  if(FTPretrieval) fprintf(fo1, "%d\n", MAX_LINES_PER_FTP);
  else             fprintf(fo1, "%d\n", MAX_LINES_PER_REQUEST);

  if(HFS_splitting)
  {
    fprintf(fo, "%s%s < %s | %s%s | %s%s | (%s%s >> result.%06ld) >>& err.log\n",
            VALD_HOME, PROG_PRESELECT, pres_in,
            VALD_HOME, PROG_SELECT,
            VALD_HOME, PROG_HFS_SPLIT,
            VALD_HOME, PROG_POST_HFS_FORMAT,
            number);
  }
  else
  {
    fprintf(fo, "%s%s < %s | (%s%s >> result.%06ld) >>& err.log\n",
            VALD_HOME, PROG_PRESELECT, pres_in,
            VALD_HOME, PROG_SELECT, number);
  }
  if(FTPretrieval)
  {
    fprintf(fo, "mv select.out %s.%06ld\n", Client_Name, number);
    fprintf(fo, "rm %s\n", pres_in);
    fprintf(fo, "gzip %s.%06ld\n", Client_Name, number);
    fprintf(fo, "mv %s.%06ld.gz %s\n", Client_Name, number, VALD_FTP_DIR);
    fprintf(fo, "chmod a+r %s/%s.%06ld.gz\n", VALD_FTP_DIR, Client_Name, number);
    if(HFS_splitting)
    {
      fprintf(fo, "mv %s %s.%06ld.bib\n", POST_HFS_BIB_FILE,
                                         Client_Name, number);
    }
    else
    {
      fprintf(fo, "mv %s %s.%06ld.bib\n", SELECT_BIB_FILE,
                                         Client_Name, number);
    }
    fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
    fprintf(fo, "mv %s.%06ld.bib.gz %s\n", Client_Name, number, VALD_FTP_DIR);
    fprintf(fo, "chmod a+r %s/%s.%06ld.bib.gz\n", VALD_FTP_DIR, Client_Name, number);
    fprintf(fo, "echo VALD processed your request number %ld >> result.%06ld\n",
            number, number);
    fprintf(fo, "echo Results can be retrieved with a Web browser at >> result.%06ld\n",
            number);
    fprintf(fo, "echo %s/%s.%06ld.gz >> result.%06ld\n",
            VALD_FTP, Client_Name, number, number);
    fprintf(fo, "echo %s/%s.%06ld.bib.gz >> result.%06ld\n",
            VALD_FTP, Client_Name, number, number);
    fprintf(fo, "echo This link will be valid for 48 hours only >> result.%06ld\n",
            number);
/*  fprintf(fo,"echo \"--==MailSection==--\" >> result.%06ld\n", number); */
  }
  else
  {
    fprintf(fo, "cat select.out >> result.%06ld\n", number);
    fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
    fprintf(fo, "rm select.out %s\n", pres_in);
    if(HFS_splitting)
    {
      fprintf(fo, "mv %s %s.%06ld.bib\n", POST_HFS_BIB_FILE,
                                         Client_Name, number);
    }
    else
    {
      fprintf(fo, "mv %s %s.%06ld.bib\n", SELECT_BIB_FILE,
                                         Client_Name, number);
    }
    fprintf(fo, "gzip %s.%06ld.bib\n", Client_Name, number);
    fprintf(fo,"echo \"Content-Disposition: attachment; filename=%s.%06ld.bib.gz;\" >> result.%06ld\n",
            Client_Name, number, number);
    fprintf(fo,"echo \"Content-Type: application/octet-stream\" >> result.%06ld\n", number);
    fprintf(fo,"echo \"Content-Transfer-Encoding: base64\" >> result.%06ld\n", number);
    fprintf(fo,"echo \"\" >> result.%06ld\n", number);
    fprintf(fo, "%s %s.%06ld.bib.gz >> result.%06ld\n", BASE64, Client_Name, number, number);
    fprintf(fo,"echo \"--==MailSection==--\" >> result.%06ld\n", number);
    fprintf(fo, "rm %s.%06ld.bib.gz\n", Client_Name, number);
  }
  return EXIT_SUCCESS;
}

int main(int n, char *filename[])
{
  int request_ON, request_type, error_state;
  long number;
  FILE *fi, *fo, *fo1, *fo2;
  char s[81], s0[81], s1[81], subject[120], *statistics_file, *ss;

  error_state=0;

  if(n<3)
  {
    printf("Usage: parserequest <request_file> <user.name>\n");
    return 0;
  }

  statistics_file=(char *)malloc(strlen(VALD_HOME)+
                        strlen(VALD_LOGS_DIR)+
			strlen(LOCAL_SITE_NAME)+18);
                        
  sprintf(statistics_file, "%s%s/%s_statistics.log",
                         VALD_HOME, VALD_LOGS_DIR, LOCAL_SITE_NAME);

  request_ON=0;
  fi=fopen(filename[1],"rt");
  if(fi == NULL) return 4;
  number=atol(filename[1]+strlen("request."));
  sprintf(s0, "job.%06ld", number);
  fo=fopen(s0, "wt"); if(fo == NULL) return 8;
  fputs("#!/bin/csh\n", fo);

/* Find BEGIN REQUEST */

  strcpy(subject,"echo \"Subject: Re: \"");
  while(fgets(s, 80, fi) != NULL)
  {
    if(!strncmp(s, "SUBJECT: ", 9) ||
       !strncmp(s, "Subject: ", 9) ||
       !strncmp(s, "subject: ", 9))
    {
      strncpy(subject+strlen(subject)-1, RemoveMeta(s)+9, 68);
      subject[strlen(subject)-1]='\"';
    }
    str2lower(compress(s1, s, 80));
    if(!strncmp(s1, "beginrequest", strlen("beginrequest")))
    {
      fprintf(fo,"%s > result.%06ld\n", subject, number);
      fprintf(fo,"echo Mime-Version: 1.0 >> result.%06ld\n", number);
      fprintf(fo,"echo \'Content-Type: multipart/mixed; boundary=\"==MailSection==\"\' >> result.%06ld\n",
              number);
      fprintf(fo,"echo \"\" >> result.%06ld\n", number);
      fprintf(fo,"echo \"--==MailSection==\" >> result.%06ld\n", number);
      fprintf(fo,"echo \'Content-Type: text/plain; charset=\"us-ascii\"\' >> result.%06ld\n", number);
      fprintf(fo,"echo \"\" >> result.%06ld\n", number);
      fprintf(fo,"echo ============= %s ============= >> result.%06ld\n",
              s0, number);
      fprintf(fo,"(%s%s %s) >> result.%06ld\n",
              VALD_HOME, PROG_TYPE_REQUEST, filename[1], number);
      fputs("touch err.log\n", fo);
      request_ON=1;

      strncpy(Client_Name, filename[2], 80);
      ss = strstr(Client_Name, "_local");
      if (ss)
      { *ss='\0';
      }

      strncpy(Personal_VALD_CONFIG, filename[2], 80);
      ss = strstr(Personal_VALD_CONFIG, "_local");
      if (ss)
      { *ss='\0';
        strcat(Personal_VALD_CONFIG, ".cfg_local");
      } else {
        strcat(Personal_VALD_CONFIG, ".cfg");
      }
      break;
    }
  }
  if(!request_ON)             /* No begin request found, forget it */
  {
    fprintf(fo,"%s > result.%06ld\n", subject, number);
    fprintf(fo,"echo Syntax error >> result.%06ld\n", number);
    fprintf(fo, "echo \"FAILED: No begin request statement\" >> result.%06ld\n",
            number);
    fclose(fi); fclose(fo);
    return EXIT_FAILURE;
  }

/* Read request type */

  request_type=UNKNOWN;
  while(fgets(s, 80, fi) != NULL)
  {
    str2lower(compress(s1, s, 80));

/* User's requests */

    if(!strncmp(s1, "showline", strlen("showline")))
    {
      request_type=SHOW_LINE;
      break;
    }
    else if(!strncmp(s1, "extractall", strlen("extractall")))
    {
      request_type=EXTRACT_ALL;
      break;
    }
    else if(!strncmp(s1, "extractelement", strlen("extractelement")))
    {
      request_type=EXTRACT_ELEMENT;
      break;
    }
    else if(!strncmp(s1, "extractstellar", strlen("extractstellar")))
    {
      request_type=EXTRACT_STELLAR;
      break;
    }
  }
  if(request_type==UNKNOWN)   /* Cannot recognise request type */
  {
    fprintf(fo, "echo FAILED: Cannot recognise request type >> result.%06ld\n",
            number);
    fclose(fi); fclose(fo);
    return EXIT_FAILURE;
  }

/* Interpret request */

  switch(request_type)
  {
    case(SHOW_LINE):       error_state = ShowLine(fi, fo, number);
                           fprintf(fo, "echo \"%ld ShowLine %s\" >> %s\n", number, Client_Name, statistics_file);
                           break;
    case(EXTRACT_ALL):     error_state = ExtractAll(fi, fo, number);
                           fprintf(fo, "echo \"%ld ExtractAll %s\" >> %s\n", number, Client_Name, statistics_file);
                           break;
    case(EXTRACT_ELEMENT): error_state = ExtractElement(fi, fo, number);
                           fprintf(fo, "echo \"%ld ExtactElement %s\" >> %s\n", number, Client_Name, statistics_file);
                           break;
    case(EXTRACT_STELLAR): error_state = ExtractStellar(fi, fo, number);
                           fprintf(fo, "echo \"%ld ExtractStellar %s\" >> %s\n", number, Client_Name, statistics_file);
                           break;
    default:               break;
  }
  fprintf(fo, "cat err.log >> result.%06ld\nrm err.log\n", number);
  fclose(fi); fclose(fo);
  if(error_state) {
    return EXIT_FAILURE; }  
  else {
    return EXIT_SUCCESS; }
}
