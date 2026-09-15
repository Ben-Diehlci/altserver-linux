#include "dns_sd.h"
#include <iostream>
#include <string>
#include <unistd.h>
#include <sys/prctl.h> // prctl(), PR_SET_PDEATHSIG
#include <signal.h> // signals
#include <sys/wait.h> // waitpid()
#include <arpa/inet.h> // ntohs()


DNSServiceErrorType DNSSD_API DNSServiceRegister
    (
    DNSServiceRef                       *sdRef,
    DNSServiceFlags                     flags,
    uint32_t                            interfaceIndex,
    const char                          *name,         /* may be NULL */
    const char                          *regtype,
    const char                          *domain,       /* may be NULL */
    const char                          *host,         /* may be NULL */
    uint16_t                            port,
    uint16_t                            txtLen,
    const void                          *txtRecord,    /* may be NULL */
    DNSServiceRegisterReply             callBack,      /* may be NULL */
    void                                *context       /* may be NULL */
    ) {
        // python3 -c 'from ctypes import *; sdRef = c_int(); CDLL('libdns_sd.so').DNSServiceRegister(byref(sdRef), flags, interfaceIndex, name, regtype, domain, host, txtLen, txtRecord, None, None)'
        std::string pyCommand = "from ctypes import *; dll = CDLL('libdns_sd.so'); ";

        pyCommand += "sdRef = c_int(); ";
#define INT_ARG(argname) (std::string("") + #argname " = " + std::to_string(argname) + "; ")
#define STR_ARG(argname) (argname ? std::string(#argname " = br'") + argname + "'; " : std::string(#argname " = None; "))
        pyCommand += INT_ARG(flags);
        pyCommand += INT_ARG(interfaceIndex);
        pyCommand += STR_ARG(name);
        pyCommand += STR_ARG(regtype);
        pyCommand += STR_ARG(domain);
        pyCommand += STR_ARG(host);
        pyCommand += INT_ARG(port);
        pyCommand += INT_ARG(txtLen);
        
        std::string txtRecordHex = "";
        for (int i = 0; i < txtLen; i++) {
            char buf[16] = { 0 };
            sprintf(buf, "\\x%02X", *((char *)txtRecord + i));
            txtRecordHex += buf;
        }
        pyCommand += "txtRecord = b'" + txtRecordHex + "'; ";
        pyCommand += "ret = dll.DNSServiceRegister(byref(sdRef), flags, interfaceIndex, name, regtype, domain, host, port, txtLen, txtRecord, None, None); ";
        pyCommand += "print('DNSServiceRegister result: %d' % ret); ";
        // Exit non-zero if registration itself failed, rather than waiting forever on a
        // registration that never happened. Without this the helper blocks on Event().wait()
        // regardless of the result, so a loadable libdns_sd.so with no avahi-daemon behind it
        // would look identical to success to the parent's liveness check below.
        pyCommand += "import sys; (sys.exit(1) if ret != 0 else None); ";

        pyCommand += "from threading import Event; Event().wait(); ";
        
        printf("Running python3 command to advertise AltServer: %s\n", pyCommand.c_str());

        pid_t ppid_before_fork = getpid();
        int child,status;
        if ((child = fork()) < 0) {
            perror("fork");
            return EXIT_FAILURE;
        }
        if(child == 0){
            int r = prctl(PR_SET_PDEATHSIG, SIGTERM);
            if (r == -1) { perror(0); exit(1); }
            // test in case the original parent exited just
            // before the prctl() call
            if (getppid() != ppid_before_fork)
                exit(1);
            execlp("python3", "python3", "-c", pyCommand.c_str(), NULL);
            exit(1);
        } else {
            // The child is meant to run forever -- the Python command ends with Event().wait().
            // So if it exits promptly, advertisement failed. Poll rather than block: a healthy
            // child never exits, and a plain waitpid() would hang here for the life of the server.
            //
            // This check matters more than its size suggests. Without it this function returned
            // success unconditionally, so a missing python3 or an unloadable libdns_sd.so left
            // AltServer running, logging nothing wrong, and completely undiscoverable by the
            // device. On an unattended headless server that is the worst possible failure mode:
            // nobody finds out until a sideloaded app expires a week later.
            bool advertised = true;

            for (int attempt = 0; attempt < 20; attempt++) // ~1 second total
            {
                usleep(50 * 1000);

                int status = 0;
                pid_t result = waitpid(child, &status, WNOHANG);

                if (result == child)
                {
                    advertised = false;
                    break;
                }

                if (result < 0)
                {
                    // Cannot tell either way; assume it is running rather than cry wolf.
                    break;
                }
            }

            if (!advertised)
            {
                fprintf(stderr,
                    "ERROR: could not advertise this server over mDNS -- the python3 helper exited\n"
                    "       immediately. AltStore on your device will NOT be able to discover this\n"
                    "       server, and refreshing will never happen.\n"
                    "       Verify with the same call this program makes:\n"
                    "           python3 -c \"from ctypes import CDLL; CDLL('libdns_sd.so')\"\n"
                    "       On Debian/Ubuntu install libavahi-compat-libdnssd-dev -- the -dev package is\n"
                    "       the one providing the unversioned libdns_sd.so symlink, not libdnssd1 -- and\n"
                    "       make sure avahi-daemon is running.\n");

                return kDNSServiceErr_Unknown;
            }

            printf("Advertising this server over mDNS as _altserver._tcp on port %d\n", ntohs(port));
        }
        return 0;
    }

int DNSSD_API DNSServiceRefSockFD(DNSServiceRef sdRef) {
    return 0xDEADBEEF;
}