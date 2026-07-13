#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <network.h>
#include <MorphOSCConfig.h>
#include <geometry.h>
#include <sensel.h>
#include <sensel_device.h>
#include <multitouch.h>
#include <tinyosc.h>
#include <sys/socket.h>

static const char* CONTACT_STATE_STRING[] = { "CONTACT_INVALID","CONTACT_START", "CONTACT_MOVE", "CONTACT_END" };
static bool enter_pressed = false;

void * waitForEnter() {
    getchar();
    enter_pressed = true;
    return 0;
}

void error(const char *msg) {
    perror(msg);
    exit(0);
}

int main(int argc, char* argv[]) {
  if (argc < 3) {
    printf("Version %d.%d\n", MorphOSC_VERSION_MAJOR, MorphOSC_VERSION_MINOR);
    printf("Usage: %s hostname port\n", argv[0]);
    exit(1);
  }

  char * address = argv[1];
  int port = atoi(argv[2]);
  int sockfd = setup_network(address, port);

  char oscbuffer[1024];

	SENSEL_HANDLE handle = NULL;
	//List of all available Sensel devices
	SenselDeviceList list;
	//SenselFrame data that will hold the contacts
	SenselFrameData *frame = NULL;

	//Get a list of available Sensel devices
	senselGetDeviceList(&list);
	if (list.num_devices == 0)
	{
		fprintf(stdout, "No device found\n");
		fprintf(stdout, "Press Enter to exit example\n");
		getchar();
		return 0;
	}

	//Open a Sensel device by the id in the SenselDeviceList, handle initialized 
	senselOpenDeviceByID(&handle, list.devices[0].idx);

	//Set the frame content to scan contact data
	senselSetFrameContent(handle, FRAME_CONTENT_CONTACTS_MASK);
	//Allocate a frame of data, must be done before reading frame data
	senselAllocateFrameData(handle, &frame);
	//Start scanning the Sensel device
  senselStartScanning(handle);
  
  fprintf(stdout, "Press Enter to exit example\n");

  pthread_t thread;
  pthread_create(&thread, NULL, waitForEnter, NULL);

  int total_contacts = 0;
  
  while (!enter_pressed) {
    unsigned int num_frames = 0;
    int len;
    //Read all available data from the Sensel device
    senselReadSensor(handle);
    //Get number of frames available in the data read from the sensor
    senselGetNumAvailableFrames(handle, &num_frames);
    for (int f = 0; f < num_frames; f++)
    {
      //Read one frame of data
      senselGetFrame(handle, frame);
      //Print out contact data
      if (frame->n_contacts != total_contacts) {
        len = tosc_writeMessage(
            oscbuffer, sizeof(oscbuffer),
            "/num_contacts",
            "i",
            frame->n_contacts);
        send(sockfd, oscbuffer, len, 0);

        total_contacts = frame->n_contacts;
      };

      if (frame->n_contacts > 0) {
        float total_force = 0;
        for (int c = 0; c < frame->n_contacts; c++)
        {
          total_force += frame->contacts[c].total_force;
        }

        float ave_dist = stretch(frame);

        len = tosc_writeMessage(
            oscbuffer, sizeof(oscbuffer),
            "/spread",
            "f",
            ave_dist);
        send(sockfd, oscbuffer, len, 0);

        len = tosc_writeMessage(
            oscbuffer, sizeof(oscbuffer),
            "/total_force",
            "f",
            total_force);
        send(sockfd, oscbuffer, len, 0);

        for (int c = 0; c < frame->n_contacts; c++)
        {
          unsigned int state = frame->contacts[c].state;

          len = tosc_writeMessage(
              oscbuffer, sizeof(oscbuffer),
              "/lifecycle",
              "is",
              frame->contacts[c].id, CONTACT_STATE_STRING[state]);
          send(sockfd, oscbuffer, len, 0);

          len = tosc_writeMessage(
              oscbuffer, sizeof(oscbuffer),
              "/x_position",
              "if",
              frame->contacts[c].id, frame->contacts[c].x_pos);
          send(sockfd, oscbuffer, len, 0);

          len = tosc_writeMessage(
              oscbuffer, sizeof(oscbuffer),
              "/y_position",
              "if",
              frame->contacts[c].id, frame->contacts[c].y_pos);
          send(sockfd, oscbuffer, len, 0);

          len = tosc_writeMessage(
              oscbuffer, sizeof(oscbuffer),
              "/force",
              "if",
              frame->contacts[c].id, frame->contacts[c].total_force);
          send(sockfd, oscbuffer, len, 0);
        }
      }
    }
  }

  return 0;
}
