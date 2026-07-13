#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netdb.h> 

int setup_network(char * address, int portno) {
  int sockfd;
  struct sockaddr_in serv_addr;
  struct hostent *server;

  sockfd = socket(AF_INET, SOCK_DGRAM, 0);

  if (sockfd < 0) {
      fprintf(stderr, "ERROR opening socket\n");
      exit(1);
  }

  server = gethostbyname(address);

  if (server == NULL) {
      fprintf(stderr,"ERROR, no such host\n");
      exit(1);
  }

  bzero((char *) &serv_addr, sizeof(serv_addr));

  serv_addr.sin_family = AF_INET;

  bcopy((char *)server->h_addr, 
       (char *)&serv_addr.sin_addr.s_addr,
       server->h_length);

  serv_addr.sin_port = htons(portno);

  if (connect(sockfd,(struct sockaddr *) &serv_addr,sizeof(serv_addr)) < 0) {
      fprintf(stderr, "ERROR connecting\n");
  }

  return sockfd;
}
