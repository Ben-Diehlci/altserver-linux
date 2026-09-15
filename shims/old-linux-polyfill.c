/* getrandom() for build hosts whose glibc predates it (added in 2.25).
 *
 * The original version of this shim leaked a file descriptor on every call -- it never closed
 * /dev/urandom on the success path, the short-read path, or the error path. AltServer is a
 * long-running daemon and its signing work calls into OpenSSL and SRP repeatedly, so the leak
 * accumulates for the life of the process and eventually exhausts the descriptor table. The
 * failure would surface far from here, as an unrelated open() or socket() failing with EMFILE.
 *
 * It also returned short reads verbatim. read() on /dev/urandom may legitimately return fewer
 * bytes than asked for, and a caller that assumes it got buflen bytes of entropy would silently
 * use an uninitialised tail. Loop instead.
 */
#include <sys/random.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

ssize_t getrandom(void *buf, size_t buflen, unsigned int flags)
{
    (void)flags; /* GRND_RANDOM / GRND_NONBLOCK are not distinguishable through /dev/urandom */

    /* O_CLOEXEC so a descriptor cannot escape into a child across the fork/exec AltServer does. */
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }

    size_t got = 0;
    while (got < buflen) {
        ssize_t n = read(fd, (unsigned char *)buf + got, buflen - got);
        if (n < 0) {
            if (errno == EINTR) {
                continue; /* interrupted before reading anything -- retry */
            }
            int saved = errno;
            close(fd);
            errno = saved; /* close() must not overwrite the reason we are failing */
            return -1;
        }
        if (n == 0) {
            break; /* /dev/urandom should never EOF; do not spin if it somehow does */
        }
        got += (size_t)n;
    }

    close(fd);
    return (ssize_t)got;
}
