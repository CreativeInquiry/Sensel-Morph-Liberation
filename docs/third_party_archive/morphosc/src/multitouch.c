#include <multitouch.h>
#include <sensel.h>
#include <sensel_device.h>
#include <tinyosc.h>
#include <sys/socket.h>


void scan_and_send(SenselFrameData * frame, SENSEL_HANDLE handle, int sockfd, char * oscbuffer, int total_contacts) {
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
