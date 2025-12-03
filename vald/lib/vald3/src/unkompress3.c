#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned char byte;
typedef unsigned int  word;

#define MAX_FILE_LENGTH   512   /* Maximum filename length */
#define LINE_LENGTH       270   /* Uncompressed size of the single transition */ 
#define LINES_PER_RECORD 1024
#define RECORD_LENGTH LINE_LENGTH*LINES_PER_RECORD /* Uncompressed record length */
#define MAX_OPEN_FILES    400   /* Maximum simultaneously open files */

unsigned char record[RECORD_LENGTH];

unsigned int
      Code,                     /* Value returned by ReadCode */
      MaxCode,	                /* Limiting value for current code size */
      ClearCode,                /* Hash table clear code */
      EOPCode,	                /* End-of-Packet code */
      BitMask,	                /* AND mask for data size */
      CurCode, OldCode, InCode, /* Decompressor variables */
      CodeSize,	                /* Uncompressed code size */
      InitCodeSize,             /* Starting code size, used during Clear */
      FinChar,                  /* Decompressor variable */
      FirstFree,                /* First free code, generated per GIF spec */
      FreeCode;	                /* Decompressor,next free slot in hash table */
int   OutCount,	                /* Decompressor output 'stack count' */
      BitOffset;                /* Bit Offset of next code */

long current_byte_in_record;    /* Byte counter in the compressed record */
unsigned long ReadMask;	        /* Code AND mask for current code size */
int change_byte_order;          /* flag to flip byte order */


/* The hash table used by the decompressor */

#define MAX_CODE_SIZE    16
#define HSIZE            ((unsigned int)(1<<MAX_CODE_SIZE))

unsigned short Prefix[HSIZE];
unsigned short Suffix[HSIZE];

/* An output array used by the decompressor */

unsigned short OutCode[HSIZE+1];

FILE *fi[MAX_OPEN_FILES];
int nfiles=0;                   /* Number of the next to be open file */

/* Array of structures describing file records */

word number_of_records[MAX_OPEN_FILES]; /* Number of records                    */
int current_record[MAX_OPEN_FILES];     /* Next record to be read               */
struct RECORD {double wl1;              /* Starting wavelength of the record    */
               double wl2;              /* Ending wavelength of the record      */
               word offset;             /* Record offset in the compressed file */
               int length;              /* Record length in the compressed file */
              } *records[MAX_OPEN_FILES];

unsigned short ReadCode(void)
{
  unsigned long RawCode;
  static unsigned long byte1, byte2, byte3, byte4;

  if(BitOffset<0)
  {
    byte1=record[current_byte_in_record++];
    BitOffset=0;
  }
  RawCode=byte1&0xFF;
  if(CodeSize+BitOffset>=8)
  {
    byte2=record[current_byte_in_record++];
    RawCode+=((byte2&0xFF)<<8);
    byte1=byte2;
  }
  if(CodeSize+BitOffset>=16)
  {
    byte3=record[current_byte_in_record++];
    RawCode+=((byte3&0xFF)<<16);
    byte1=byte3;
  }
  if(CodeSize+BitOffset>=24)
  {
    byte4=record[current_byte_in_record++];
    RawCode+=((byte4&0xFF)<<24);
    byte1=byte4;
  }
  RawCode>>=BitOffset;
  BitOffset+=CodeSize;
  BitOffset%=8;
  return (unsigned short)(RawCode&ReadMask);
}

char *ByteSwap(char *s, int n)
{
  char c; int i, j;

  for(i=0, j=n-1; i<n/2; i++,j--)
  {
    c=s[i]; s[i]=s[j]; s[j]=c;
  }
  return s;
}

#define ADDLINE(CHAR) line[nbytes++]=CHAR;                 \
  if((nbytes%LINE_LENGTH)==0)                              \
  {                                                        \
    if(change_byte_order)                                  \
    {                                                      \
      wl[nlines]        =*(double *)ByteSwap(line,    8);  \
      element[nlines]   =*(int    *)ByteSwap(line+ 8, 4);  \
      loggf[nlines]     =*(float  *)ByteSwap(line+12, 4);  \
      e_low[nlines]     =*(double *)ByteSwap(line+16, 8);  \
      j_low[nlines]     =*(float  *)ByteSwap(line+24, 4);  \
      e_high[nlines]    =*(double *)ByteSwap(line+28, 8);  \
      j_high[nlines]    =*(float  *)ByteSwap(line+36, 4);  \
      lande_low[nlines] =*(float  *)ByteSwap(line+40, 4);  \
      lande_high[nlines]=*(float  *)ByteSwap(line+44, 4);  \
      gamrad[nlines]    =*(float  *)ByteSwap(line+48, 4);  \
      gamst[nlines]     =*(float  *)ByteSwap(line+52, 4);  \
      gamvw[nlines]     =*(float  *)ByteSwap(line+56, 4);  \
      memcpy(str+nlines*210, line+60, 210);                \
      if(line[236]<48) /* This record has multiple refs */ \
      {                /* Swap bytes in integer pointers*/ \
        str[nlines*210+177]=line[238];                     \
        str[nlines*210+178]=line[237];                     \
        str[nlines*210+179]=line[240];                     \
        str[nlines*210+180]=line[239];                     \
        str[nlines*210+181]=line[242];                     \
        str[nlines*210+182]=line[241];                     \
      }                                                    \
    }                                                      \
    else                                                   \
    {                                                      \
      wl[nlines]        =*(double *)line;                  \
      element[nlines]   =*(int    *)(line+ 8);             \
      loggf[nlines]     =*(float  *)(line+12);             \
      e_low[nlines]     =*(double *)(line+16);             \
      j_low[nlines]     =*(float  *)(line+24);             \
      e_high[nlines]    =*(double *)(line+28);             \
      j_high[nlines]    =*(float  *)(line+36);             \
      lande_low[nlines] =*(float  *)(line+40);             \
      lande_high[nlines]=*(float  *)(line+44);             \
      gamrad[nlines]    =*(float  *)(line+48);             \
      gamst[nlines]     =*(float  *)(line+52);             \
      gamvw[nlines]     =*(float  *)(line+56);             \
      memcpy(str+nlines*210, line+60, 210);                \
    }                                                      \
    nbytes=0; nlines++;                                    \
  }

/*
    printf("%d %16.14g %d %g %g %g\n", nlines, wl[nlines],element[nlines], \
            loggf[nlines],e_low[nlines],lande_high[nlines]);   \
*/


int uncompress(long length, double *wl, int *element,
               float *j_low, double *e_low, float *j_high, double *e_high,
               float *loggf, float *gamrad, float *gamst,
               float *gamvw, float *lande_low, float *lande_high,
               char *str)
{
  short  i, nbytes;
  int nlines, l;
  char line[LINE_LENGTH];

  nbytes=0; nlines=0; OutCount=0; BitOffset=-1;

/* First we set the intial code size and compute decompressor constant 
 * values, based on this code size.
 */

  CodeSize=8;
  ClearCode=(1<<CodeSize);
  EOPCode=ClearCode+1;
  FreeCode=FirstFree=ClearCode+2;

  CodeSize++;
  InitCodeSize=CodeSize;
  MaxCode=(1<<CodeSize);
  ReadMask=MaxCode-1;
  BitMask=0xFF;

  Code=ClearCode;
  for(l=0; l<length; l++)
  {
/*
   Clear code sets everything back to its initial value, then reads the
   immediately subsequent code as uncompressed data.
*/
    if(Code==ClearCode)
    {
      CodeSize=InitCodeSize;
      MaxCode=(1<<CodeSize);
      ReadMask=MaxCode-1;
      FreeCode=FirstFree;
      Code=ReadCode();
      CurCode=OldCode=Code;
      FinChar=CurCode&BitMask;
      ADDLINE(FinChar)
    }
    else
    {

/* If not a clear code, must be data: save same as CurCode and InCode */
/* if we're at maxcode and didn't get a clear, stop loading */

      if(FreeCode>=HSIZE) exit(16);
      CurCode=InCode=Code;

/*
   If greater or equal to FreeCode, not in the hash table yet;
   repeat the last character decoded
*/

      if(CurCode>=FreeCode)
      {
        CurCode=OldCode;
        if(OutCount>HSIZE) exit(16);
        OutCode[OutCount++]=FinChar;
      }

/*
   Unless this code is raw data, pursue the chain pointed to by CurCode
   through the hash table to its end; each code in the chain puts its
   associated output code on the output queue.
*/

      while(CurCode>BitMask)
      {
        if(OutCount>HSIZE) exit(16);  /* corrupt file */
        OutCode[OutCount++]=Suffix[CurCode];
        CurCode=Prefix[CurCode];
      }
      if(OutCount>HSIZE) exit(16);

/* The last code in the chain is treated as raw data. */

      FinChar=CurCode&BitMask;
      OutCode[OutCount++]=FinChar;

/* Now we put the data out to the Output routine.
 * It's been stacked LIFO, so deal with it that way...
 */

      for(i=OutCount-1; i>=0; i--)
      {
        ADDLINE(OutCode[i])
      }
      OutCount=0;

/* Build the hash table on-the-fly. No table is stored in the file. */

      Prefix[FreeCode]=OldCode;
      Suffix[FreeCode]=FinChar;
      OldCode=InCode;

/* Point to the next slot in the table.  If we exceed the current
 * MaxCode value, increment the code size unless it's already MAX_CODE_SIZE.
 * If it is, do nothing: the next code decompressed better be CLEAR
 */

      FreeCode++;
      if(FreeCode>=MaxCode)
      {
        if(CodeSize<MAX_CODE_SIZE)
        {
          CodeSize++;
          MaxCode*=2;
          ReadMask=(1<<CodeSize)-1;
        }
      }
    }
    Code=ReadCode();
    if(Code==EOPCode) break;
  }
  return nlines;
}

int ukopen_(int *ifile, char *file_data, char *file_descr)
{
  int i, f; char *c; FILE *fd;
  char filename[MAX_FILE_LENGTH];

  if(nfiles==0)                      /* Initialize file pointers */
  {
    nfiles=1; for(i=0; i<MAX_OPEN_FILES; i++) fi[i]=NULL;
  }

  f=*ifile;
  if(f>=MAX_OPEN_FILES || f<0 || fi[f]!=NULL)
    return -1;                  /* Check if we can open one more */

  i=1;
  change_byte_order=(*((char *)(&i)))?0:1;  /* Check if big-endian than need to change byte order */
  c=strchr(file_descr, ' '); if(c==NULL) return -4;
  strncpy(filename, file_descr, c-file_descr); filename[c-file_descr]='\0';

  fd=fopen(filename, "rb"); if(fd==NULL) return -2;  /* Open descriptor file */
  i=fread(&number_of_records[f], sizeof(word), 1, fd);  /* Get the number of records */
  if(change_byte_order) ByteSwap((char *)(&number_of_records[f]), sizeof(word));
  records[f]=(struct RECORD *)malloc(number_of_records[f]*sizeof(struct RECORD));
  i=fread(records[f], sizeof(struct RECORD), number_of_records[f], fd);
  if(change_byte_order)
  {
    for(i=0; i<number_of_records[f]; i++)
    {
      ByteSwap((char *)&(records[f][i].wl1),    sizeof(double));
      ByteSwap((char *)&(records[f][i].wl2),    sizeof(double));
      ByteSwap((char *)&(records[f][i].offset), sizeof(word));
      ByteSwap((char *)&(records[f][i].length), sizeof(int));
    }
  }
  fclose(fd);

/*
  for(i=0; i<number_of_records[f]; i++)
  {
    printf("File:%d Record:%d Wl1:%g Wl2:%g Off:%d Length:%d\n",
            f,i,records[f][i].wl1,records[f][i].wl2,
            records[f][i].offset,records[f][i].length);
  }
*/

  c=strchr(file_data, ' '); if(c==NULL) return -4;
  strncpy(filename, file_data, c-file_data); filename[c-file_data]='\0';

  fi[f]=fopen(filename, "rb");                  /* Open input file */
  if(fi[f]==NULL) return -3;
  current_record[f]=0;
  return number_of_records[f];
}

int ukclose_(int *ifile)
{
  int f;
  f=*ifile;
  if(f>=MAX_OPEN_FILES || f<0 || fi[f]==NULL)
    return -1;                  /* Check if it's a valid file */
  fclose(fi[f]); fi[f]=NULL;
  free(records[f]);
  return 0;
}


int ukread_(int *ifile, double *ptwave1, double *ptwave2,
            double *wl, int *element,
            float *j_low, double *e_low, float *j_high, double *e_high,
            float *loggf, float *gamrad, float *gamst,
            float *gamvw, float *lande_low, float *lande_high,
            char *str)
{
  int i, j, k, f, nlines;
  long length;
  double wave1, wave2;

  f=*ifile;
  if(f>=MAX_OPEN_FILES || f<0 || fi[f]==NULL)
    return -1;                  /* Check if it's a valid file */

/* Find starting record number */

  wave1=*ptwave1; wave2=*ptwave2;
  i=0; j=number_of_records[f]-1;
/*  printf("%g %g %g %g\n",wave1,records[f][j].wl2,
                           wave2,records[f][i].wl1); */
  if(wave1>records[f][j].wl2 || wave2<records[f][i].wl1) return -2;
  else if(wave1<records[f][i].wl1) k=i;
  else
  {
    k=i;
    while(j-i>1)
    {
      k=(i+j)/2;
      if(wave1<records[f][k].wl1) j=k; else i=k;
    }
    k=(wave1>records[f][i].wl2)?j:i;
  }
  current_record[f]=k;        /* Locate current record */
  
  if(fseek(fi[f], records[f][current_record[f]].offset, SEEK_SET)) return -3;
  length=records[f][current_record[f]].length;
  if(fread(record, length, 1, fi[f])!=1) return -4; /* Read record */
  current_byte_in_record=0;
  nlines=uncompress(length, wl, element, j_low, e_low, j_high, e_high,
                    loggf, gamrad, gamst, gamvw, lande_low, lande_high,
                    str);

  /* Filter the decompressed data by wavelength range */
  int filtered_count = 0;
  for(i = 0; i < nlines; i++) {
    if(wl[i] >= wave1 && wl[i] <= wave2) {
      if(filtered_count != i) {
        /* Move this line to the filtered position */
        wl[filtered_count] = wl[i];
        element[filtered_count] = element[i];
        j_low[filtered_count] = j_low[i];
        e_low[filtered_count] = e_low[i];
        j_high[filtered_count] = j_high[i];
        e_high[filtered_count] = e_high[i];
        loggf[filtered_count] = loggf[i];
        gamrad[filtered_count] = gamrad[i];
        gamst[filtered_count] = gamst[i];
        gamvw[filtered_count] = gamvw[i];
        lande_low[filtered_count] = lande_low[i];
        lande_high[filtered_count] = lande_high[i];
        /* Copy string data (76 characters per line) */
        for(j = 0; j < 76; j++) {
          str[filtered_count * 76 + j] = str[i * 76 + j];
        }
      }
      filtered_count++;
    }
  }

/*
  printf("Offset: %d %d\n",length, current_byte_in_record);
 */

/*
  for(i=0;i<filtered_count;i++)
  {
    printf("%d %f\n",i,wl[i]);
  }
  printf("\n");
  for(i=0;i<filtered_count;i++)
  {
    for(j=0;j<76;j++) printf("%c",*(str+76*i+j));
    printf("\n");
  }
*/

  current_record[f]++;
  return filtered_count;
}

int uknext_(int *ifile, double *wl, int *element,
           float *j_low, double *e_low, float *j_high, double *e_high,
           float *loggf, float *gamrad, float *gamst,
           float *gamvw, float *lande_low, float *lande_high,
           char *str)
{
  long length, nlines;
  int f;

  f=*ifile;
  if(f>=MAX_OPEN_FILES || f<0 || fi[f]==NULL)
    return -1;                  /* Check if it's a valid file */

/*
  printf(">Starting next record %d %d\n",length,current_record[f]);
*/

  if(current_record[f]>=number_of_records[f]) return -2;
  length=records[f][current_record[f]].length;
  if(fread(record, length, 1, fi[f])!=1) return -3; /* Read record */
  current_byte_in_record=0;

  nlines=uncompress(length, wl, element, j_low, e_low, j_high, e_high,
                    loggf, gamrad, gamst, gamvw, lande_low, lande_high,
                    str);
/*
  printf("<Finishing next record %d %d %d\n",length,current_record[f], nlines);
*/
  current_record[f]++;

  return nlines;
}
