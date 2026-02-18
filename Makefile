obj-m += wave3ctl_kmod.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

install: all
	sudo insmod wave3ctl_kmod.ko
	@echo "✓ /dev/wave3ctl ready"

uninstall:
	-sudo rmmod wave3ctl_kmod
