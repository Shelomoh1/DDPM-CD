import logging
from collections import OrderedDict

import torch
import torch.nn as nn
import os
import model.networks as networks
from .base_model import BaseModel
logger = logging.getLogger('base')


class CD(BaseModel):
    def __init__(self, opt):
        super(CD, self).__init__(opt)
        # define network and load pretrained models
        self.netCD = self.set_device(networks.define_CD(opt))

        # set loss and load resume state
        self.loss_type = opt['model_cd']['loss_type']
        if self.loss_type == 'ce':
            self.loss_func =nn.CrossEntropyLoss().to(self.device)
        else:
            raise NotImplementedError()
        
        if self.opt['phase'] == 'train':
            self.netCD.train()
            # find the parameters to optimize
            optim_cd_params = list(self.netCD.parameters())

            if opt['train']["optimizer"]["type"] == "adam":
                self.optCD = torch.optim.Adam(
                    optim_cd_params, lr=opt['train']["optimizer"]["lr"])
            elif opt['train']["optimizer"]["type"] == "adamw":
                self.optCD = torch.optim.AdamW(
                    optim_cd_params, lr=opt['train']["optimizer"]["lr"])
            else:
                raise NotImplementedError(
                    'Optimizer [{:s}] not implemented'.format(opt['train']["optimizer"]["type"]))

            self.log_dict = OrderedDict()
        self.load_network()
        self.print_network()

    def feed_data(self, feats_A, feats_B, train_data):
        self.feats_A = feats_A
        self.feats_B = feats_B
        self.train_data    = self.set_device(train_data)

    def optimize_parameters(self):
        self.optCD.zero_grad()
        self.pred_cm = self.netCD(self.feats_A, self.feats_B)
        l_cd = self.loss_func(self.pred_cm, self.train_data["L"].long())
        # need to average in multi-gpu
        l_cd.backward()
        self.optCD.step()

        # set log
        self.log_dict['l_cd'] = l_cd.item()

    def test(self):
        self.netCD.eval()
        with torch.no_grad():
            if isinstance(self.netCD, nn.DataParallel):
                self.pred_cm = self.netCD.module.forward(self.feats_A, self.feats_B)
            else:
                self.pred_cm = self.netCD(self.feats_A, self.feats_B)
        self.netCD.train()


    def get_current_log(self):
        return self.log_dict

    def get_current_visuals(self):
        out_dict = OrderedDict()
        out_dict['pred_cm'] = torch.argmax(self.pred_cm, dim=1, keepdim=False)
        out_dict['gt_cm'] = self.train_data['L']
        return out_dict

    def print_network(self):
        s, n = self.get_network_description(self.netCD)
        if isinstance(self.netCD, nn.DataParallel):
            net_struc_str = '{} - {}'.format(self.netCD.__class__.__name__,
                                             self.netCD.module.__class__.__name__)
        else:
            net_struc_str = '{}'.format(self.netCD.__class__.__name__)

        logger.info(
            'Change Detection Network structure: {}, with parameters: {:,d}'.format(net_struc_str, n))
        logger.info(s)

    def save_network(self, epoch, iter_step):
        cd_gen_path = os.path.join(
            self.opt['path']['checkpoint'], 'CD_I{}_E{}_gen.pth'.format(iter_step, epoch))
        cd_opt_path = os.path.join(
            self.opt['path']['checkpoint'], 'CD_I{}_E{}_opt.pth'.format(iter_step, epoch))
        # gen
        network = self.netCD
        if isinstance(self.netCD, nn.DataParallel):
            network = network.module
        state_dict = network.state_dict()
        for key, param in state_dict.items():
            state_dict[key] = param.cpu()
        torch.save(state_dict, cd_gen_path)
        # opt
        opt_state = {'epoch': epoch, 'iter': iter_step,
                     'scheduler': None, 'optimizer': None}
        opt_state['optimizer'] = self.optCD.state_dict()
        torch.save(opt_state, cd_opt_path)

        logger.info(
            'Saved CD model in [{:s}] ...'.format(cd_gen_path))

    def load_network(self):
        load_path = self.opt['path_cd']['resume_state']
        if load_path is not None:
            logger.info(
                'Loading pretrained model for CD model [{:s}] ...'.format(load_path))
            gen_path = '{}_gen.pth'.format(load_path)
            opt_path = '{}_opt.pth'.format(load_path)
            # gen
            network = self.netCD
            if isinstance(self.netCD, nn.DataParallel):
                network = network.module
            network.load_state_dict(torch.load(
                gen_path), strict=True)
                
            if self.opt['phase'] == 'train':
                #optimizer
                opt = torch.load(opt_path)
                self.optCD.load_state_dict(opt['optimizer'])
                self.begin_step = opt['iter']
                self.begin_epoch = opt['epoch']