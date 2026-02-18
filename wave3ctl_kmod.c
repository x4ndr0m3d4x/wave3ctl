/*
 * wave3ctl - USB Audio Class control proxy for Elgato Wave:3
 *
 * Sends control requests via usb_control_msg(), bypassing the
 * usbdevfs interface-claim check that blocks userspace access.
 * The snd-usb-audio driver stays loaded — audio is unaffected.
 */
#include <linux/module.h>
#include <linux/usb.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/slab.h>

#define WAVE3_VID  0x0fd9
#define WAVE3_PID  0x0070

struct wave3_xfer {
	__u8  request_type;
	__u8  request;
	__u16 value;
	__u16 index;
	__u16 length;       /* in: max bytes; out: actual bytes */
	__u8  data[64];
} __packed;

#define WAVE3_CTL _IOWR('W', 0, struct wave3_xfer)

/* ── find the Wave:3 on the USB bus ────────────────────────────── */

struct find_ctx { struct usb_device *dev; };

static int match_wave3(struct usb_device *dev, void *data)
{
	struct find_ctx *ctx = data;

	if (le16_to_cpu(dev->descriptor.idVendor)  == WAVE3_VID &&
	    le16_to_cpu(dev->descriptor.idProduct) == WAVE3_PID) {
		ctx->dev = usb_get_dev(dev);
		return 1;          /* stop iterating */
	}
	return 0;
}

/* ── ioctl handler ─────────────────────────────────────────────── */

static long wave3_ioctl(struct file *filp, unsigned int cmd,
			unsigned long arg)
{
	struct wave3_xfer xfer;
	struct find_ctx ctx = { .dev = NULL };
	unsigned char *buf;
	unsigned int pipe;
	int ret;

	if (cmd != WAVE3_CTL)
		return -ENOTTY;
	if (copy_from_user(&xfer, (void __user *)arg, sizeof(xfer)))
		return -EFAULT;
	if (xfer.length > sizeof(xfer.data))
		return -EINVAL;

	usb_for_each_dev(&ctx, match_wave3);
	if (!ctx.dev)
		return -ENODEV;

	buf = kmalloc(xfer.length, GFP_KERNEL);
	if (!buf) { ret = -ENOMEM; goto put; }

	if (xfer.request_type & USB_DIR_IN) {
		pipe = usb_rcvctrlpipe(ctx.dev, 0);
	} else {
		pipe = usb_sndctrlpipe(ctx.dev, 0);
		memcpy(buf, xfer.data, xfer.length);
	}

	ret = usb_control_msg(ctx.dev, pipe,
			      xfer.request, xfer.request_type,
			      xfer.value, xfer.index,
			      buf, xfer.length, 1000 /* ms */);

	if (ret >= 0 && (xfer.request_type & USB_DIR_IN)) {
		xfer.length = ret;
		memcpy(xfer.data, buf, ret);
		if (copy_to_user((void __user *)arg, &xfer, sizeof(xfer)))
			ret = -EFAULT;
		else
			ret = 0;
	}

	kfree(buf);
put:
	usb_put_dev(ctx.dev);
	return ret < 0 ? ret : 0;
}

/* ── misc device setup ─────────────────────────────────────────── */

static const struct file_operations wave3_fops = {
	.owner          = THIS_MODULE,
	.unlocked_ioctl = wave3_ioctl,
};

static struct miscdevice wave3_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name  = "wave3ctl",
	.fops  = &wave3_fops,
	.mode  = 0666,
};

static int __init wave3_init(void) { return misc_register(&wave3_misc); }
static void __exit wave3_exit(void) { misc_deregister(&wave3_misc); }

module_init(wave3_init);
module_exit(wave3_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("USB Audio Class control proxy for Elgato Wave:3");
MODULE_VERSION("1.0");
