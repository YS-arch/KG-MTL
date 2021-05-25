
from model import CPI
from model import DTI,CPI_GCN,CPI_DGLLife
from dgl.data.utils import save_graphs, load_graphs
from data_loader import load_data
import utils
import numpy as np
import random
import time
import os
import torch
import torch.nn.functional as F
import argparse
import warnings
warnings.filterwarnings("ignore")
import wandb
def get_data(features, drug2smiles, target2seq):
    drugs = list()
    targets = list()
    labels = list()
    drugids = list()
    for (drugid, targetid, label) in features:
        drugs.append(drug2smiles[int(drugid)])
        targets.append(target2seq[int(targetid)])
        labels.append([int(label)])
        drugids.append(drugid)

    return np.array(drugs), np.array(targets), np.array(labels), np.array(drugids)
def cpi_data_iter(batch_size, features, drug2smile=None, target2seq=None):
    num_examples = len(features)
    indices = list(range(num_examples))
    random.shuffle(indices)
    features = torch.from_numpy(np.array(features))
    for i in range(0, num_examples, batch_size):
        drugs = list()
        targets = list()
        labels = list()
        drugids = list()
        j = torch.LongTensor(indices[i:min(i+batch_size, num_examples)])
        features_select = features.index_select(0, j)
        for (drugid, targetid, label) in features_select:
            drugs.append(drug2smile[int(drugid)])
            targets.append(target2seq[int(targetid)])
            labels.append([int(label)])
            drugids.append(drugid)
        yield np.array(drugs), np.array(targets), np.array(labels), np.array(drugids)


def train_cpi(dataset):
    data = load_data('dataset/kg',
                     'dataset/dti_task', 'dataset/cpi_task',cpi_dataset=dataset)

    model = CPI(200, 1500, 3, 0.1)
    val_compounds, val_proteins, val_cpi_labels, val_compoundids = get_data(
        data.val_cpi_set, data.compound2smiles, data.protein2seq)
    test_compounds, test_proteins, test_cpi_labels, test_compoundids = get_data(
        data.test_cpi_set, data.compound2smiles, data.protein2seq)
    
    val_cpi_log=[]
    epochs_his=[]
    torch.cuda.set_device(1)
    
    
    best_performance=dict()
    tests_performance=dict()
    lr_list=[0.01,0.005,0.001,0.0008,0.0005,0.0001] 
    test_cpi_labels = torch.from_numpy(test_cpi_labels).float()
    for lr in lr_list:
        best_roc=0.0
        optimizer_global = torch.optim.Adam(model.parameters(), lr=lr)
        model_path='ckl/comparision_lr{}_epoch100_dataset_{}.pkl'.format(lr,dataset)
        for epoch in range(100):
            model.cuda()
            model.train()
            for (compounds, proteins, cpi_labels, compoundids) in cpi_data_iter(256,data.train_cpi_set, data.compound2smiles, data.protein2seq):
                cpi_pred= model(torch.LongTensor(compounds).cuda(), torch.LongTensor(proteins).cuda())
                cpi_labels = torch.from_numpy(cpi_labels).float().cuda()

                loss_cpi = F.binary_cross_entropy(cpi_pred, cpi_labels)

                loss_cpi.backward()
                optimizer_global.step()
                optimizer_global.zero_grad()
                print('epoch: {}, Loss: {:.4f}'.format(epoch,loss_cpi))
            model.cpu()
            model.eval()
            cpi_pred= model(torch.LongTensor(val_compounds), torch.LongTensor(val_proteins))
            cpi_labels = torch.from_numpy(val_cpi_labels).float()
            val_acc, val_roc, val_pre, val_recall,val_aupr = utils.eval_cpi_2(
                    cpi_pred, cpi_labels)
            print("Epoch {:04d}-CPI-val | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                      format(epoch, val_acc, val_roc, val_pre, val_recall, val_aupr))
            val_cpi_log.append([val_acc,val_roc,val_pre,val_recall,val_aupr])
            if best_roc<val_roc:
                best_roc=val_roc
                print('Best performance: {:.4f}'.format(best_roc))
                best_performance[lr]=[val_acc,val_roc,val_pre,val_recall,val_aupr]
                torch.save(model.state_dict(),model_path)
            epochs_his.append(epoch)

        model.load_state_dict(torch.load(model_path))
        model.cpu()
        model.eval()

        test_cpi_pred= model(torch.LongTensor(test_compounds), torch.LongTensor(test_proteins))
        
        test_acc, test_roc, test_pre, test_recall,test_aupr = utils.eval_cpi_2(
                test_cpi_pred, test_cpi_labels)
        print("Test CPI | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                  format(test_acc, test_roc, test_pre, test_recall,test_aupr))
        tests_performance[lr]=[test_acc, test_roc, test_pre, test_recall,test_aupr]

    # val_cpi_log=np.array(val_cpi_log)
    # epochs_his=np.array(epochs_his)
    # x_label='epoch'
    # y_label='performance'
    # labels=['acc','roc','precision','recall','aupr']
    # #utils.draw_line(val_dti_log,epochs_his,'val_dti_performance.png',x_label=x_label,y_label=y_label,labels=labels)
    # utils.draw_line(val_cpi_log,epochs_his,'Celegan_val_cpi_performance_comparision',x_label=x_label,y_label=y_label,labels=labels)
    utils.Log_Writer('logs/comparision_cpi_performance.log',str(best_performance))
    utils.Log_Writer('logs/comparision_cpi_performance.log',str(tests_performance))


def get_dti_data(features):
    drugs = list()
    targets = list()
    labels = list()
    drugids = list()
    for (drugid, targetid, label) in features:
        drugs.append(int(drugid))
        targets.append(int(targetid))
        labels.append([int(label)])
    return np.array(drugs), np.array(targets), np.array(labels)

def process_kg(args,train_kg,data,adj_list,degrees,use_cuda,sample_nodes=None):
    g, node_id, edge_type, node_norm, grapg_data, labels = utils.generate_sampled_graph_and_labels(
            train_kg, args.graph_batch_size, args.graph_split_size, data.num_rels, adj_list, degrees, args.negative_sample, args.edge_sampler, sample_nodes)

    #print('Done edge sampling for rgcn')
    node_id = torch.from_numpy(node_id).view(-1, 1).long()
    edge_type = torch.from_numpy(edge_type)
    edge_norm = utils.node_norm_to_edge_norm(
        g, torch.from_numpy(node_norm).view(-1, 1))
    grapg_data, labels = torch.from_numpy(
        grapg_data), torch.from_numpy(labels)
    deg = g.in_degrees(range(g.number_of_nodes())).float().view(-1, 1)
    if use_cuda:
        
        node_id, deg = node_id.cuda(), deg.cuda()
        edge_norm, edge_type = edge_norm.cuda(), edge_type.cuda()
        grapg_data, labels = grapg_data.cuda(), labels.cuda()
        # test_node_id,test_deg=test_node_id.cuda(),test_deg.cuda()
        # test_norm,test_rel=test_norm.cuda(),test_rel.cuda()
    return g,node_id,edge_type,node_norm,grapg_data,labels,edge_norm

def graph_data_iter(batch_size,features,protein2seq):
    num_examples = len(features)
    indices = list(range(num_examples))
    random.shuffle(indices)
    features = torch.from_numpy(np.array(features))
    for i in range(0, num_examples, batch_size):
        drugs = list()
        targets = list()
        labels = list()
        drugids = list()
        j = torch.LongTensor(indices[i:min(i+batch_size, num_examples)])
        features_select = features.index_select(0, j)
        for (drugid, targetid, label) in features_select:
            drugs.append(int(drugid))
            targets.append(protein2seq[int(targetid)])
            labels.append([int(label)])
            drugids.append(drugid)
        yield drugs, np.array(targets), np.array(labels), np.array(drugids)

def get_all_graph(features,protein2seq):
    drugs=list()
    targets=list()
    labels=list()
    drugids=list()
    for (drugid,targetid,label) in features:
        drugs.append(int(drugid))
        targets.append(protein2seq[int(targetid)])
        labels.append([int(label)])
        drugids.append(drugid)
    return drugs, np.array(targets), np.array(labels), np.array(drugids)

def test(model, val_dataset, protein2seq, smiles2graph):
    y_preds=[]
    y_lables=[]
    for drugs, proteins, cpi_labels,_ in graph_data_iter(1,val_dataset,protein2seq):
        y_pred=model(drugs,torch.from_numpy(np.array(proteins)),smiles2graph,True)
        y_preds.append(float(y_pred))
        y_lables.append(int(cpi_labels))
    y_preds=torch.from_numpy(np.array(y_preds))
    y_lables=torch.from_numpy(np.array(y_lables))
    val_acc, val_roc, val_pre, val_recall,val_aupr = utils.eval_cpi_2(
                    y_preds, y_lables)
    print("CPI-val | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                  format( val_acc, val_roc, val_pre, val_recall, val_aupr))

def train_cpi_gcn(dataset,args):
    data = load_data('dataset/kg',
                     'dataset/dti_task', 'dataset/cpi_task',cpi_dataset=dataset,cpi_gnn=True)
    val_cpi_log=[]
    epochs_his=[]
    best_record=[0.0,0.0]
    best_performance=0.0
    model_path='ckl/comparision_batch_size32_lr0.005_gcn_epoch100.pkl'
    val_compounds,val_proteins,val_cpi_label,cpi_drugids=get_all_graph(data.val_set_gnn,data.protein2seq)
    test_compounds,test_proteins,test_cpi_label,test_cpi_drugids=get_all_graph(data.test_set_gnn,data.protein2seq)
    val_cpi_label=torch.from_numpy(val_cpi_label)
    test_cpi_label=torch.from_numpy(test_cpi_label)
    num_feature=78
    drug_size=200
    hidden_dim=200
    model=CPI_DGLLife(num_feature,hidden_dim,drug_size,data.word_length)
    wandb.watch(model)
    torch.cuda.set_device(0)
    optimizer_global = torch.optim.Adam(model.parameters(), lr=0.001)
    early_stop=0
    loss_history=[]
    auc_history=[]
    for epoch in range(100):
        if early_stop>=100:
            print('After 6 consecutive epochs, the model stops training because the performance has not improved!')
            break
        early_stop+=1
        model.cuda()
        model.train()
        loss_log=0.0
        count=0
        for drugs, proteins, cpi_labels,_ in graph_data_iter(64,data.train_set_gnn,data.protein2seq):
            cpi_pred=model(drugs,torch.from_numpy(np.array(proteins)).cuda(),data.smiles2graph)
            cpi_labels = torch.from_numpy(cpi_labels).float().cuda()
            loss_cpi = F.binary_cross_entropy(cpi_pred, cpi_labels)
            
            loss_cpi.backward()
            optimizer_global.step()
            optimizer_global.zero_grad()
            loss_log+=loss_cpi
            count+=1
        #loss_history.append(loss_log.detach().cpu())
        print('epoch: {}, Loss: {:.4f}'.format(epoch,loss_log/count))

        model.cpu()
        model.eval()
        #test(model,data.val_set_gnn,data.protein2seq,data.smiles2graph)
        cpi_pred=model(val_compounds,torch.from_numpy(val_proteins),data.smiles2graph,True)
        val_acc, val_roc, val_pre, val_recall,val_aupr = utils.eval_cpi_2(
                    cpi_pred, val_cpi_label)
        auc_history.append(val_roc)
        if best_performance<val_roc:
            early_stop=0
            best_performance=val_roc
            print('Best performance: {:.4f}'.format(best_performance))
            torch.save(model.state_dict(),model_path)
        test_cpi_pred= model(test_compounds,torch.from_numpy(test_proteins),data.smiles2graph,True)
        test_acc, test_roc, test_pre, test_recall,test_aupr = utils.eval_cpi_2(
                test_cpi_pred, test_cpi_label)
        loss_log=loss_log/count
        logs={'cpi_loss': loss_log, 'cpi_acc': val_acc, 'cpi_auc': val_roc, 'cpi_aupr': val_aupr,  'test_cpi_acc':test_acc,'test_cpi_auc':test_roc,'test_cpi_aupr':test_aupr}
        wandb.log(logs)
        if best_record[1]<test_roc:
            best_record=[test_acc, test_roc, test_pre, test_recall,test_aupr]
        
        print("Test CPI | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                  format(test_acc, test_roc, test_pre, test_recall,test_aupr))
        print("Epoch {:04d}-CPI-val | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                  format(epoch, val_acc, val_roc, val_pre, val_recall, val_aupr))
        # val_cpi_log.append([val_acc,val_roc,val_pre,val_recall,val_aupr])
        # epochs_his.append(epoch)
    
    # np.save('cpi_single_{}_loss.npy'.format(dataset),np.array(loss_history))
    # np.save('cpi_single_{}_auc.npy'.format(dataset),np.array(auc_history))
    model.load_state_dict(torch.load(model_path))
    model.cpu()
    model.eval()
    test_cpi_pred= model(test_compounds,torch.from_numpy(test_proteins),data.smiles2graph,True)
    
    test_acc, test_roc, test_pre, test_recall,test_aupr = utils.eval_cpi_2(
            test_cpi_pred, test_cpi_label)
    print("Test CPI | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
              format(test_acc, test_roc, test_pre, test_recall,test_aupr))
    return [test_acc, test_roc, test_aupr]
    # print('Best performance:')
    # print(best_record)

 
def train_dti(args):
    data = load_data('dataset/kg',
                     'dataset/dti_task', 'dataset/cpi_task',cpi_dataset='human',dti_dataset=args.dataset)
    val_drugs, val_targets, val_dti_labels = get_dti_data(data.val_dti_set)
    
    val_dti_labels = torch.from_numpy(val_dti_labels).long()
    test_drugs, test_targets, test_dti_labels =get_dti_data(data.test_dti_set)
    test_dti_labels=torch.from_numpy(test_dti_labels)
    model = DTI(data.num_nodes,
                  200, 200, data.num_rels, 20)
    #wandb.watch(model,log=None)
    torch.cuda.set_device(0)
    train_kg = torch.LongTensor(np.array(data.train_kg))   
    loss_history=[]
    print('build adj and degrees....')

    if os.path.isfile('data/adj_list.npy'):
        adj_list = list(np.load('data/adj_list.npy', allow_pickle=True))
        degrees = np.load('data/degrees.npy')
    else:
        adj_list, degrees = utils.get_adj_and_degrees(data.num_nodes, train_kg)
        np.save('data/adj_list.npy', np.array(adj_list))
        np.save('data/degrees.npy', degrees)
    
    optimizer_global = torch.optim.Adam(model.parameters(), lr=0.001)
    val_dti_log = []
    best_performance_dti=0.0
    model_path='ckl/comparision_lr0.001_{}batch_size32_dti_single_best.pkl'.format(args.dataset)
    epochs_his=[]
    test_performance=dict()
    use_cuda=True 
    early_stop=0
    best_record=[0.0,0.0]
    #loss_history=[]
    for epoch in range(100):
        if early_stop>=100:
            print('After 6 consecutive epochs, the model stops training because the performance has not improved!')
            break
        
        early_stop+=1
        model.cuda()
        model.train()
        g,node_id,edge_type,node_norm,grapg_data,labels,edge_norm=process_kg(args,train_kg,data,adj_list,degrees,use_cuda,sample_nodes=list(data.sample_nodes))
        #print('Done sampleing end.')
        drug_entities, target_entities, dti_labels = get_dti_data(
            data.train_dti_set)
        dti_labels = torch.from_numpy(dti_labels).float().cuda()
        loss_epoch_total=0
        #for (compounds, proteins, cpi_labels, compoundids) in cpi_data_iter(32,data.train_cpi_set, data.compound2smiles, data.protein2seq):
        for i in range(64):
            dti_pred,embed=model(drug_entities,target_entities,g, node_id, edge_type, edge_norm)
            dti_loss=F.binary_cross_entropy(dti_pred,dti_labels)
            #loss_history.append(dti_loss)
            dti_loss.backward()
            optimizer_global.step()
            optimizer_global.zero_grad()
            loss_epoch_total+=dti_loss
        loss_history.append(float(loss_epoch_total))
        print('epoch: {}, Loss: {:.4f}'.format(epoch,loss_epoch_total))
        model.cpu()
        model.eval()
        dti_pred,_= model(val_drugs, val_targets, g, node_id.cpu(), edge_type.cpu(), edge_norm.cpu())
        loss_history.append(loss_epoch_total)
        val_acc, val_roc, val_pre, val_recall,val_aupr = utils.eval_cpi_2(
                dti_pred, val_dti_labels)
        print("Epoch {:04d}-DTI-val | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                  format(epoch, val_acc, val_roc, val_pre, val_recall, val_aupr))
        val_dti_log.append([val_acc,val_roc,val_pre,val_recall,val_aupr])

        epochs_his.append(epoch) 
        if best_performance_dti<val_roc:
            early_stop=0
            best_performance_dti=val_roc
            print('Best performance: {:.4f}'.format(best_performance_dti))
            torch.save(model.state_dict(),model_path)        
            #print('test....')
        test_dti_pred, _=model(test_drugs,test_targets, g, node_id.cpu(), edge_type.cpu(), edge_norm.cpu())
        test_acc, test_roc, test_pre, test_recall,test_aupr = utils.eval_cpi_2(
                    test_dti_pred, test_dti_labels)
        if best_record[1]<test_roc:
            best_record=[test_acc, test_roc, test_pre, test_recall,test_aupr]
        print("DTI-test | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                      format(test_acc, test_roc, test_pre, test_recall,test_aupr))
        test_performance[epoch]=[test_acc, test_roc, test_pre, test_recall,test_aupr]
        logs={'dti_loss':loss_epoch_total/64 , 'dti_acc': val_acc, 'dti_auc': val_roc, 'dti_aupr': val_aupr,'test_dti_acc': test_acc, 'test_dti_auc':test_roc, 'test_dti_aupr': test_aupr}
        wandb.log(logs)
    
    print('best_record:')
    print(best_record)
    model.load_state_dict(torch.load(model_path))
    model.cpu()
    model.eval()
    test_dti_pred, _=model(test_drugs,test_targets, g, node_id.cpu(), edge_type.cpu(), edge_norm.cpu())
    test_acc, test_roc, test_pre, test_recall,test_aupr = utils.eval_cpi_2(
                test_dti_pred, test_dti_labels)
    print("DTI-test-final | acc:{:.4f}, roc:{:.4f}, precision:{:.4f}, recall:{:.4f}, aupr:{:.4f}".
                  format(test_acc, test_roc, test_pre, test_recall,test_aupr))
    #np.save('dti_single_loss_{}.npy'.format(args.dataset),np.array(loss_history))
    return [test_acc, test_roc, test_aupr]


def CPI_func(dataset): 
    return train_cpi(dataset)

def DTI_func(args):
    return train_dti(args)

def CPI_GNN_func(dataset):
    parser = argparse.ArgumentParser()
    parser.add_argument('--dropout',type=float,default=0.2,help='dropout probability')
    parser.add_argument("--gpu", type=int, default=-1,
                        help="which GPU to use. Set -1 to use CPU.")
    parser.add_argument("--epochs", type=int, default=200,
                        help="number of training epochs")
    parser.add_argument("--num-heads", type=int, default=8,
                        help="number of hidden attention heads")
    parser.add_argument("--num-out-heads", type=int, default=1,
                        help="number of output attention heads")
    parser.add_argument("--num-layers", type=int, default=1,
                        help="number of hidden layers")
    parser.add_argument("--num-hidden", type=int, default=8,
                        help="number of hidden units")
    parser.add_argument("--residual", action="store_true", default=False,
                        help="use residual connection")
    parser.add_argument("--in-drop", type=float, default=.6,
                        help="input feature dropout")
    parser.add_argument("--attn-drop", type=float, default=.6,
                        help="attention dropout")
    parser.add_argument("--lr", type=float, default=0.005,
                        help="learning rate")
    parser.add_argument('--weight-decay', type=float, default=5e-4,
                        help="weight decay")
    parser.add_argument('--negative-slope', type=float, default=0.2,
                        help="the negative slope of leaky relu")
    args=parser.parse_args()
    print(args)
    return train_cpi_gcn(dataset,args)



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dropout', type=float,
                        default=0.2, help='dropout probability')
    parser.add_argument('--n-hidden', type=int, default=500,
                        help='number of hidden units')
    parser.add_argument('--gpu', type=int, default=0, help='gpu id')
    parser.add_argument('--lr_pre', type=float, default=1e-2,
                        help='learning rate of pretrain')
    parser.add_argument('--lr_dti', type=float, default=0.001,
                        help='learning rate of dti task')
    parser.add_argument('--n_bases', type=int, default=20,
                        help='number of weight blocks for each relation')
    parser.add_argument('--dti-batch-size', type=int,
                        default=128, help='batch size for dti task')
    parser.add_argument('--sample_size', type=int,
                        default=4, help='size of sample of ')
    parser.add_argument("--n-layers", type=int, default=2,
                        help="number of propagation rounds")
    parser.add_argument("--n-epochs", type=int, default=100,
                        help="number of minimum training epochs")
    parser.add_argument("--eval-batch-size", type=int,
                        default=500, help="batch size when evaluating")

    parser.add_argument("--eval-protocol", type=str, default="filtered",
                        help="type of evaluation protocol: 'raw' or 'filtered' mrr")

    parser.add_argument("--regularization", type=float,
                        default=0.01, help="regularization weight")
    parser.add_argument("--grad-norm", type=float,
                        default=1.0, help="norm to clip gradient to")
    # parser.add_argument("--graph-batch-size", type=int, default=30000,
    #                     help="number of edges to sample in each iteration")
    parser.add_argument("--graph-split-size", type=float, default=0.5,
                        help="portion of edges used as positive sample")
    parser.add_argument("--negative-sample", type=int, default=10,
                        help="number of negative samples per positive sample")
    parser.add_argument("--edge-sampler", type=str, default="neighbor",
                        help="type of edge sampler: 'uniform' or 'neighbor'")
    parser.add_argument("--graph_batch_size", type=int, default=40000)
    parser.add_argument("--rgcn_epochs", type=int,
                        default=10, help="rgcn pre-training rounds")
    parser.add_argument("--loss_lamda", type=float,
                        default=0.5, help="rgcn pre-training rounds")
    parser.add_argument("--dataset",type=str,default='human',help='dataset for dti task')
    parser.add_argument("--task",type=str,default='cpi',help='[cpi, dti]')
    args = parser.parse_args()
    wandb.init(project='make-cpi',tags='kg-mtl-single',config=args)
    results=[]
    if args.task=='cpi':
        result=CPI_GNN_func(args.dataset)
    elif args.task=='dti':
        result=DTI_func(args)
    else:
        raise Exception('please input correct task [cpi, dti]')
        #results.append(result)
    wandb.finish()